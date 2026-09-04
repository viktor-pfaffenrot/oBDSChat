# Architecture decision records

These records explain the major choices behind oBDSChat. They capture the
constraints that led to each choice, the alternatives that were considered, and
the trade-offs that future changes must account for. Read them before replacing
a dependency, moving a system boundary, or adding infrastructure.

All records on this page are accepted unless their status says otherwise. A
record describes why a decision was made; the linked explanation and reference
pages describe the current implementation.

## ADR-001: Use PostgreSQL instead of SQLite

**Status:** Accepted

### Context

The application needs persistent storage for sections of the
Umsetzungsleitfaden and may later store other official oBDS prose sources. It is
containerized and intended to resemble a deployable application rather than a
single-user local script.

### Decision and rationale

Use PostgreSQL as the application database. PostgreSQL is well established, provides multi-user storage, source and oBDS-version filtering, and support for both the current
`pg_search` extension and a possible future `pgvector` extension for semantic RAG. It allows metadata, lexical retrieval, and a later vector index to remain in one service.

This choice keeps deployment realistic without requiring a separate search
engine. Docker Compose can provide the database, initialize it, check its
health, and persist its data in the same way on every host.

### Alternatives considered

- SQLite would be simpler for a single process, but it does not provide the
  required PostgreSQL search extensions or the same multi-service deployment
  model.
- Separate database and search services would isolate those responsibilities,
  but they would add synchronization and operational overhead before the corpus
  requires it.

### Consequences

The application needs a running database service, persistent storage,
initialization, and health checks. In return, one database can serve structured
metadata and ranked prose retrieval. See [How source data is stored](data-storage.md)
and the [database reference](../database/index.md) for the current design.

## ADR-002: Use pg_search BM25 instead of native PostgreSQL full-text search

**Status:** Accepted

### Context

The Umsetzungsleitfaden contains exact oBDS field names, German medical terms,
rare technical expressions, and sections of different lengths. Search must rank
the most useful sections rather than merely identify every section containing a
term.

### Decision and rationale

Use ParadeDB's `pg_search` extension and BM25 for lexical retrieval. BM25 uses
corpus-level term rarity and document-length normalization, which gives a strong
ranking baseline for this terminology-heavy corpus. Keeping the index in
PostgreSQL also avoids a second search service and its synchronization path.

The backend exposes this capability through a small domain function instead of
leaking index details into callers:

```python
search_umsetzungsleitfaden(query, version=None, limit=5)
```

### Alternatives considered

- Native PostgreSQL `tsvector` and `ts_rank` would avoid a specialized
  extension, but provide less suitable ranking behavior for this corpus.
- Vector-only retrieval would improve some semantic matches, but weaken the
  simple, explainable baseline for exact technical vocabulary.
- Elasticsearch or OpenSearch would provide mature search features, but add a
  service that the current project size does not justify.
- BM25 is typically combined with semantic search in a hybrid search via reciprocal rank fusion. The current setup can naturally be extented with vector search.

### Consequences

Rare terms and field-name matches receive useful lexical weight without external
search infrastructure. The database image is more specialized, however:
`pg_search` must be installed and loaded, and tests of real search behavior need
a compatible PostgreSQL instance. The index and query details are described in
[How source data is stored](data-storage.md).

## ADR-003: Query the oBDS XSD with deterministic tools

**Status:** Accepted

### Context

The XSD contains authoritative facts such as element names, XML paths,
datatypes, cardinalities, enumerations, and parent-child relationships. Those
facts should not depend on probabilistic retrieval or on a model remembering the
schema correctly.

### Decision and rationale

Keep the versioned XSD files as the source of truth and query them through
oBDS-specific deterministic functions backed by `xmlschema` and `lxml`. The
public tool boundary includes functions such as:

```python
search_schema(...)
get_schema_concept_locations(...)
get_schema_element(...)
get_schema_values(...)
get_schema_cardinality(...)
```

`search_schema` is ranked (e.g. element containing query ranks higher than documentations mentions query) and limited for discovery.
`get_schema_concept_locations` instead returns every location for questions about
concepts. It identifies the containing message type and prefers element-name or named-datatype matches, falling back to documentation and enumeration meanings only when no structural match exists.

The implementation lives in `src/backend/xsd.py`, while `src/backend/tools.py`
exposes the functions to the model. This thin layer delegates XSD semantics
to established libraries, gives the model domain-oriented operations, and makes
schema answers exact and testable.

### Alternatives considered

- Chunking the XSD as plain text would lose structural meaning and make exact
  facts dependent on retrieval quality.
- A generic XML or XPath tool would expose more power than the model needs and
  make correct tool use harder.
- A custom XSD parser would duplicate complex namespace, reference, and type
  semantics already handled by libraries.

### Consequences

Formal schema facts remain separate from prose guidance and can be verified
deterministically. The wrapper still has to handle versions, namespaces,
references, paths, and source locations correctly. Its design is described in
[How the backend works](backend-architecture.md#versioned-xsd-catalog).

## ADR-004: Use Requesty as the model-routing boundary

**Status:** Accepted

### Context

The project needs to compare proprietary and open-weight models, select global
or EU-hosted inference, and preserve a path to future on-premises inference. The
rest of the application should not depend on the concrete provider selected for
one evaluation or deployment.

### Decision and rationale

Use Requesty as the hosted model-routing boundary and keep provider selection in
runtime configuration. The backend calls the stable `policy/obdschat` route;
concrete model and routing changes belong to the provider policy rather than the
application code. Direct OpenAI configuration remains available for controlled
testing.

The common boundary makes model switching and regional provider selection
possible without changing retrieval, tool execution, or public HTTP contracts.
It also allows hosted open-weight candidates to be benchmarked before committing
to self-hosted inference infrastructure.

### Alternatives considered

- Direct integrations for every provider would expose provider differences
  throughout the backend.
- Supporting only OpenAI would make comparisons and later deployment choices
  harder.

### Consequences

The backend remains mostly provider-independent and models can be compared
without an infrastructure rewrite. Requesty is an external runtime dependency,
and provider behavior can still vary behind a common API. Strict EU residency
requires both an EU Requesty gateway and an EU-hosted inference provider. See
the [runtime configuration reference](../reference/runtime-configuration.md)
for the current settings.

## ADR-005: Target OpenAI-compatible Chat Completions tool calling

**Status:** Accepted

### Context

Tool calling must work across proprietary models, open-weight models, Requesty,
and possible future vLLM or SGLang deployments. A widely implemented protocol is
more valuable here than a provider-specific feature set.

### Decision and rationale

Use the modern OpenAI-compatible Chat Completions tool-calling convention:

```text
tools
tool_choice
assistant.tool_calls
role="tool"
tool_call_id
```

Do not use the deprecated `functions` and `function_call` interface. The chosen
contract is supported by hosted routers and common open-weight serving stacks,
so the backend can keep one explicit model-tool loop across hosted and future
self-hosted deployments.

### Alternatives considered

- The OpenAI Responses API alone would couple the application to an interface
  that is not as broadly available across the target serving stacks.
- Provider-specific tool APIs would require multiple orchestration paths.
- Custom JSON prompting would move validation into prompt interpretation and be
  less reliable than structured tool calls.

### Consequences

The application gains a portable and mockable model contract. Some advanced
provider features need separate treatment, and models (verified for glm-5.2) can differ in tool-call reliability even when they accept the same schema. Some models (e.g. QWEN-3.8) might still require model-specific implementation of the tool-loop.

## ADR-006: Separate the Gradio frontend from the FastAPI backend

**Status:** Accepted

### Context

The project needs a simple interactive interface while keeping model calls,
retrieval, database access, XSD access, and source traceability on the server
side.

### Decision and rationale

Run the Gradio frontend and FastAPI backend as separate services that communicate
over HTTP:

```text
Gradio frontend
    |
    | HTTP
    v
FastAPI backend
```

The frontend must not import backend internals. FastAPI remains the single owner
of domain behavior and the frontend becomes a replaceable HTTP client. Each side
can therefore be deployed and tested independently.

### Alternatives considered

- One combined Gradio application would reduce the number of services, but tie
  presentation directly to domain and infrastructure code.
- Importing backend Python modules into the frontend would avoid HTTP calls, but
  erase the application boundary and couple their dependencies.

### Consequences

Responsibilities and deployment boundaries remain clear, and a future frontend
can reuse the same API. Public response models live in the neutral
`obdschat_api` package, so both services validate the same contract without the
frontend importing backend internals. Response-contract changes require rebuilding both service images. See [How the frontend works](frontend-architecture.md) for the state and
contract implications.

## ADR-007: Start with BM25 before vector search

**Status:** Accepted

### Context

The Umsetzungsleitfaden contains many exact field names and technical terms, so
lexical retrieval has a strong chance of solving the initial use cases. Vector
retrieval may help semantic paraphrases, but it also needs an embedding model,
an indexing pipeline, and separate quality evaluation.

### Decision and rationale

Start with BM25 only. Add vector retrieval later by using e.g.`pgvector` inside PostgreSQL and evaluate a hybrid strategy against the BM25 baseline.

### Alternatives considered

- Vector-only search would discard the strong exact-term baseline.
- Building hybrid retrieval immediately might improve some queries, but would
  make it harder to implement and mentain.
- Treating BM25 as permanently sufficient would prevent evidence-driven
  improvement when lexical overlap is genuinely weak.

### Consequences

The application has no embedding pipeline or vector index today. Semantic
paraphrases with little lexical overlap may be missed. That limitation is
accepted until evaluation demonstrates that the added system complexity produces
a meaningful gain.

## ADR-008: Make evaluation a first-class design constraint

**Status:** Accepted

### Context

Generic model benchmarks do not measure the complete oBDS workflow: German
technical language, tool choice, XSD reasoning, and BM25 retrieval all matter. A few manually selected examples would not provide a stable basis for model or retrieval decisions.

### Decision and rationale

Maintain an application-specific set of curreently 71 realistic oBDS questions. Evaluate model behavior across tool selection, tool argument validity,
multi-tool completion, groundedness, correct abstention, unsupported claims, and
German answer quality.

Use GPT-5.6 Luna as the reference-quality model when comparing open-weight
candidates. Requesty policy can select concrete candidates without changing the
backend.

The current production-style runner reads `tests/questions.yaml`, exercises the
same tools as the application, and reports answer and citation correctness.
Keeping the suite in the repository makes expected facts and sources reviewable
alongside code changes.

### Alternatives considered

- Public benchmarks would be cheaper to consume, but would not represent the
  project's source and tool boundaries.
- Ad hoc manual questions would help exploration, but would not make changes
  comparable over time.

### Consequences

Model, prompt, tool, and retrieval changes can be judged with project-specific
evidence. Maintaining representative questions and carefully reviewed expected
facts requires ongoing work, and live model evaluations have external cost and
some provider variability. The tests are currently lexical, not semantic. That is, some answers to questions, in particular ambiguous or unanswerable questions, are harder to verify and tests might procude inflated false negatives.

## Maintaining these records

Add an ADR when a change:

- replaces a major dependency;
- moves a system boundary;
- changes the retrieval or model-provider strategy;
- introduces substantial infrastructure; or
- reverses a previously accepted choice.

Do not add ADRs for ordinary implementation details. When a decision changes,
keep its history and change its status to `Superseded by ADR-XXX` instead of
deleting it. Add the replacement as a new numbered record with its own context
and consequences.
