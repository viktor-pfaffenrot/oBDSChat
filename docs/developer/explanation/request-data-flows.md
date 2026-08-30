# How requests and data move through oBDSChat

Three flows explain most changes: source synchronization, question answering, and
exact XSD evidence viewing.

## Source synchronization

```mermaid
sequenceDiagram
    participant S as Source synchronizer
    participant X as basisdatensatz.de
    participant C as Public Confluence
    participant F as XSD volume
    participant D as ParadeDB

    S->>X: Discover and download oBDS 3.x schemas
    S->>S: Validate filename, version, XML, and XSD root
    S->>C: Enumerate UMK pages and fetch storage HTML
    S->>S: Validate responses and extract heading-sized documents
    Note over S: All remote fetches finish before writes start
    S->>F: Atomically replace changed versioned XSD files
    S->>D: Ensure bootstrap schema exists
    S->>D: Delete old guide rows and insert new rows in one transaction
```

Outbound URLs and redirects are restricted to the expected official hosts. The
synchronizer rejects empty corpora and malformed source metadata. PostgreSQL
replacement affects only rows whose source type is `umsetzungsleitfaden`.

## Question answering

```mermaid
sequenceDiagram
    participant U as Browser
    participant F as Frontend
    participant A as FastAPI backend

    U->>F: Submit question
    F->>F: Render pending state
    F->>A: Question, version, and bounded history
    A->>A: Validate request and run grounded answering
    A->>A: Verify citations and build response
    A-->>F: Answer, used versions, and sources
    F-->>U: Render answer and evidence cards
```

The backend's model, tool, and evidence processing is described in
[How the backend works](backend-architecture.md#runtime-data-flow).

If the API explicitly constrains `obds_version`, every version-aware tool call is
forced to that version. Otherwise, model tool arguments can select a version;
missing selection resolves to the newest available XSD version.

Questions asking where or how a concept can be reported, which message types
contain it, or for all occurrences use exhaustive concept-location lookup. That
lookup covers every structural match in the selected schema version instead of
using the ranked, limited schema search.

Provider waits do not block the backend event loop, and synchronous version and
tool work is offloaded to worker threads. Separate requests can overlap. Within
one request, model rounds and tool calls remain ordered; answers are not streamed.
The frontend applies its configured per-process concurrency limit, but direct API
clients bypass that frontend limit.

The backend returns only sources named by the model and verified against current
tool executions. Search results that were read but not cited do not appear in the
response.

## Exact XSD evidence view

```mermaid
sequenceDiagram
    participant U as Browser
    participant F as Frontend XSD route
    participant A as Backend evidence route
    participant C as Cached schema catalog

    U->>F: Open version and exact XML path
    F->>A: Request typed XSD evidence
    A->>C: Exact version/path lookup
    C->>C: Parse and index version on first use
    C-->>A: Facts plus bounded numbered source lines
    A-->>F: Validated evidence JSON
    F-->>U: Escaped field view
```

The evidence excerpt includes three context lines around the declaration and is
bounded to 160 lines. Metadata reports the full declaration range and whether the
displayed declaration was truncated.

## Failure propagation

- Frontend transport failures become a user-safe backend-unreachable message.
- Backend validation failures retain HTTP client-error meaning.
- Model failures become a bad-gateway response.
- database, runtime configuration, and XSD parsing failures become
  service-unavailable responses.
- Frontend response-model mismatch becomes an invalid-backend-response message.

Health routes are intentionally outside these dependency flows; they report only
whether their own process can answer HTTP.
