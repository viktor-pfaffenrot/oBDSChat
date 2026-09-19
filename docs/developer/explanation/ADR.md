# Architecture decision records

These accepted records capture the major decisions behind oBDSChat and why they
were made. Linked explanation and reference pages describe the implementation.

## ADR-001: Use PostgreSQL instead of SQLite

The application needs persistent, multi-user storage for Umsetzungsleitfaden
sections and potentially other official oBDS prose sources in a containerized
deployment. Use PostgreSQL to keep metadata, source and oBDS-version filtering,
`pg_search` retrieval, and a possible future `pgvector` index in one service.
Docker Compose provides consistent initialization, health checks, and persistent
storage across hosts.

### Considered Options

- SQLite simplifies single-process storage but lacks the required PostgreSQL
  search extensions and the same multi-service deployment model.
- Separate database and search services add synchronization and operational
  overhead before the corpus requires them.

### Consequences

The application requires a running database service and persistent storage. See
[How source data is stored](data-storage.md) and the
[database reference](../database/index.md).

## ADR-002: Use pg_search BM25 instead of native PostgreSQL full-text search

The Umsetzungsleitfaden contains exact oBDS field names, German medical terms,
rare expressions, and sections of different lengths. Use ParadeDB's `pg_search`
extension and BM25 for lexical retrieval because corpus-level term rarity and
document-length normalization provide a strong ranking baseline for this corpus.
Keeping the index in PostgreSQL avoids a separate search service and its
synchronization path.

### Considered Options

- Native PostgreSQL `tsvector` and `ts_rank` avoid a specialized extension but
  provide less suitable ranking behavior for this corpus.
- Vector-only retrieval may improve semantic matches but weakens the explainable
  baseline for exact technical vocabulary.
- Elasticsearch or OpenSearch add a service that the current project size does
  not justify.

### Consequences

The database image must install and load `pg_search`, and real search tests need
compatible PostgreSQL. A small domain function hides index details from callers.
The setup can later combine BM25 and vector search through reciprocal rank fusion;
see ADR-007 and [How source data is stored](data-storage.md).

## ADR-003: Query the oBDS XSD with deterministic tools

The XSD defines authoritative element names, XML paths, datatypes, cardinalities,
enumerations, and parent-child relationships. Keep versioned XSD files as the
source of truth and query them through oBDS-specific deterministic functions
backed by `xmlschema` and `lxml`, so formal schema facts remain exact, testable,
and separate from prose retrieval. Domain-oriented tools delegate XSD semantics
to established libraries instead of relying on model memory.

### Considered Options

- Chunking the XSD as plain text loses structure and makes exact facts depend on
  retrieval quality.
- Generic XML or XPath tools expose more power than the model needs and make
  correct tool use harder.
- A custom parser duplicates complex namespace, reference, and type semantics
  already handled by libraries.

### Consequences

The wrapper must handle versions, namespaces, references, paths, and source
locations correctly. Ranked, limited `search_schema` serves discovery;
`get_schema_concept_locations` returns every concept location, identifies its
containing message type, and prefers element-name or named-datatype matches,
falling back to documentation and enumeration meanings only when no structural
match exists. Dedicated tools expose element details, values, and cardinalities.
See [How the backend works](backend-architecture.md#versioned-xsd-catalog).

## ADR-004: Use Requesty as the model-routing boundary

The project needs to compare proprietary and open-weight models, select global
or EU-hosted inference, and preserve a path to on-premises inference. Use Requesty
as the hosted routing boundary, with the backend calling `policy/obdschat` and
concrete model selection controlled by runtime configuration and provider policy.
This keeps retrieval, tool execution, and public HTTP contracts independent of
model switching and allows hosted candidates to be benchmarked before investing
in self-hosted inference.

### Considered Options

- Direct integrations for every provider spread provider differences throughout
  the backend.
- Supporting only OpenAI restricts comparisons and later deployment choices.

### Consequences

Requesty becomes an external runtime dependency, and provider behavior can still
vary behind the common API. Strict EU residency requires both an EU Requesty
gateway and an EU-hosted inference provider. Direct OpenAI configuration remains
available for controlled testing; see the
[runtime configuration reference](../reference/runtime-configuration.md).

## ADR-005: Target OpenAI-compatible Chat Completions tool calling

Tool calling must work across proprietary models, open-weight models, Requesty,
and possible future vLLM or SGLang deployments. Use the OpenAI-compatible Chat
Completions contract (`tools`, `tool_choice`, `assistant.tool_calls`,
`role="tool"`, and `tool_call_id`) rather than the deprecated `functions` and
`function_call` interface. This widely implemented contract supports one explicit,
portable, mockable model-tool loop across hosted and future self-hosted deployments.

### Considered Options

- Using only the OpenAI Responses API couples the application to an interface
  less broadly available across the target serving stacks.
- Provider-specific tool APIs require multiple orchestration paths.
- Custom JSON prompting moves validation into prompt interpretation and is less
  reliable than structured tool calls.

### Consequences

Advanced provider features may need separate treatment. Accepting the same schema
does not guarantee equal tool-call reliability, as observed with glm-5.2;
models such as QWEN-3.8 may still require model-specific loop handling.

## ADR-006: Separate the Gradio frontend from the FastAPI backend

The project needs an interactive interface while keeping model calls, retrieval,
database access, XSD access, and source traceability on the server. Run Gradio
and FastAPI as separate services communicating over HTTP, with FastAPI owning
domain behavior and the frontend acting as a replaceable client that never
imports backend internals. This allows independent deployment and testing and
lets future frontends reuse the API.

### Considered Options

- A combined Gradio application reduces service count but ties presentation to
  domain and infrastructure code.
- Importing backend modules into the frontend avoids HTTP calls but erases the
  application boundary and couples dependencies.

### Consequences

Public response models live in the neutral `obdschat_api` package, so both
services validate the same contract without backend imports. Response-contract
changes require rebuilding both service images. See
[How the frontend works](frontend-architecture.md).

## ADR-007: Start with BM25 before vector search

Exact field names and technical terms make lexical retrieval a strong initial
baseline for the Umsetzungsleitfaden, while vector retrieval requires an embedding
model, an indexing pipeline, and separate quality evaluation. Start with BM25
only and add vector retrieval, for example through PostgreSQL's `pgvector`, when
evaluation demonstrates a meaningful gain. Evaluate any hybrid strategy against
the BM25 baseline.

### Considered Options

- Vector-only search discards the strong exact-term baseline.
- Immediate hybrid retrieval adds implementation and maintenance complexity.
- Treating BM25 as permanently sufficient prevents evidence-driven improvement
  when lexical overlap is weak.

### Consequences

The application has no embedding pipeline or vector index. Semantic paraphrases
with little lexical overlap may be missed until evaluation justifies the added
complexity.

## ADR-008: Make evaluation a first-class design constraint

Generic benchmarks and a few manual examples cannot reliably assess German oBDS
language, tool choice, XSD reasoning, and BM25 retrieval together. Maintain a
repository-owned suite of realistic oBDS questions covering tool selection,
argument validity, multi-tool completion, groundedness, correct abstention,
unsupported claims, and German answer quality. Use GPT-5.6 Luna as the
reference-quality model for open-weight comparisons, with Requesty policy
selecting candidates without backend changes.

### Considered Options

- Public benchmarks are cheaper to consume but do not represent the project's
  source and tool boundaries.
- Ad hoc manual questions aid exploration but do not make changes comparable
  over time.

### Consequences

The recorded suite contains 71 questions in `tests/questions.yaml`; its
production-style runner exercises application tools and reports answer and
citation correctness. Keeping expected facts and sources reviewable alongside
code requires ongoing maintenance, while live evaluations incur external cost
and provider variability. Current checks are lexical rather than semantic and
may produce inflated false negatives, particularly for ambiguous or unanswerable
questions.

## Maintaining these records

Add an ADR only when a decision is hard to reverse, surprising without context,
and the result of a real trade-off. Record its context, decision, and rationale
in one to three sentences; add Considered Options or Consequences only when they
preserve useful rejected alternatives or non-obvious downstream effects.

New individual records belong in `internal_docs/adr/` as `0001-slug.md`,
`0002-slug.md`, and so on, incrementing the highest existing number. Compile them
into this page only when requested. When revisiting a decision, use optional
`status` frontmatter (`proposed`, `accepted`, `deprecated`, or
`superseded by ADR-NNNN`); preserve the old record and add its replacement with a
new number.
