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

Use PostgreSQL as the application database. PostgreSQL provides multi-user
storage, source and oBDS-version filtering, and support for both the current
`pg_search` extension and a possible future `pgvector` extension. It allows
metadata, lexical retrieval, and a later vector index to remain in one service.

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
get_schema_element(...)
get_schema_values(...)
get_schema_cardinality(...)
```

The implementation lives in `src/backend/xsd.py`, while `src/backend/tools.py`
exposes the bounded functions to the model. This thin layer delegates XSD
semantics to established libraries, gives the model domain-oriented operations,
and makes schema answers exact and testable.

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
- Self-hosting an open-weight model from the start would add substantial
  infrastructure before evaluation establishes that it is useful.

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
provider features need separate treatment, and models can differ in tool-call
reliability even when they accept the same schema. The current loop and its
validation rules are covered in
[How the backend works](backend-architecture.md#model-loop-and-citation-policy).

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
can reuse the same API. The split adds an HTTP client, duplicated boundary
models, and one network hop. See [How the frontend works](frontend-architecture.md)
for the state and contract implications.

## ADR-007: Start with BM25 before vector search

**Status:** Accepted

### Context

The Umsetzungsleitfaden contains many exact field names and technical terms, so
lexical retrieval has a strong chance of solving the initial use cases. Vector
retrieval may help semantic paraphrases, but it also needs an embedding model,
an indexing pipeline, and separate quality evaluation.

### Decision and rationale

Start with BM25 only. Add vector retrieval only when the application-specific
evaluation set shows meaningful failures caused by vocabulary mismatch. If that
happens, prefer `pgvector` inside PostgreSQL and evaluate a hybrid strategy
against the BM25 baseline.

This is a sequencing decision, distinct from ADR-002's choice of lexical search
engine. It prevents an unmeasured feature from becoming permanent infrastructure
and keeps the initial retrieval path easy to inspect and debug.

### Alternatives considered

- Vector-only search would discard the strong exact-term baseline.
- Building hybrid retrieval immediately might improve some queries, but would
  make it harder to attribute quality gains and failures.
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
technical language, tool choice, XSD reasoning, BM25 query formulation,
grounding, and correct abstention all matter. A few manually selected examples
would not provide a stable basis for model or retrieval decisions.

### Decision and rationale

Maintain an application-specific set of approximately 50 to 100 realistic oBDS
questions. Evaluate model behavior across tool selection, tool argument validity,
multi-tool completion, groundedness, correct abstention, unsupported claims, and
German answer quality. Evaluate retrieval separately with `Recall@1`,
`Recall@3`, `Recall@5`, and mean reciprocal rank.

Use GPT-5.6 Luna as the reference-quality model when comparing open-weight
candidates. The model name is a comparison baseline, not an application
dependency; Requesty policy can select concrete candidates without changing the
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
some provider variability.

## ADR-009: Use Docker Compose for deployable infrastructure

**Status:** Accepted

### Context

The application should run consistently on a developer machine and on a
single-host deployment. Its services have explicit startup dependencies, but the
MVP does not need a distributed orchestrator.

### Decision and rationale

Use Docker Compose for the MVP deployment. Compose defines the frontend,
backend, source synchronizer, and PostgreSQL services, while the model remains an
external service reached through the configured provider.

This is sufficient for the current size of the project. It makes service
boundaries, health checks, persistent storage, and startup ordering reproducible
without introducing a second orchestration platform.

### Alternatives considered

- Manual local processes would be lighter, but leave dependencies and startup
  order to each operator.
- Kubernetes would provide stronger distributed orchestration, but its
  operational cost is not justified by this single-host MVP.
- Separate deployment manifests would increase the number of configurations
  that must stay aligned.

### Consequences

Local and deployed environments share one understandable topology. Compose is
not intended to be the final answer for a large distributed deployment, and
production hardening may eventually require additional infrastructure. See
[How oBDSChat is structured](system-architecture.md) and
[How to deploy and upgrade oBDSChat](../how-to/deploy.md).

## ADR-010: Keep the architecture deliberately small

**Status:** Accepted

### Context

oBDSChat is an engineering experiment and a portfolio project. Its important
problems are deterministic XSD querying, retrieval, model tool use, grounding,
and evaluation. Premature layers would make those behaviors harder to follow
without yet solving a demonstrated maintenance problem.

### Decision and rationale

Keep the backend as a small set of explicit modules:

```text
app.py
config.py
db.py
llm.py
search.py
tools.py
xsd.py
```

Add service, repository, adapter, port, or use-case layers only when the current
modules become difficult to maintain. Narrow typed boundaries are still
required; the decision rejects speculative abstraction, not structure.

### Alternatives considered

A layered architecture with `services/`, `repositories/`, `adapters/`, `ports/`,
and `use_cases/` could support a larger system. For the current codebase it would
mostly distribute straightforward behavior across more files and indirection.

### Consequences

The backend stays easy to read, explain, and test. Modules may eventually need
to split as responsibilities or team ownership grow. When that happens, add the
smallest boundary justified by the observed pressure and record a new decision
if it materially changes the architecture. The current ownership map is in the
[repository structure](../reference/repository-structure.md).

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
