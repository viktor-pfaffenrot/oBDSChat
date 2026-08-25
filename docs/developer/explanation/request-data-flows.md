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
    participant M as Model provider
    participant T as Local tools
    participant E as XSD / ParadeDB evidence

    U->>F: Submit question
    F->>F: Store pending state and render progress
    F->>A: Typed question plus bounded history
    A->>A: Validate input and resolve version context
    A->>M: System policy, history, question, strict tools
    M->>T: Required first tool call
    T->>E: Exact lookup or bounded search
    E-->>T: Official source facts
    T-->>M: JSON result with citation IDs
    loop Optional additional tool rounds
        M->>T: Tool call
        T->>E: Retrieve evidence
        E-->>M: Serialized result
    end
    M-->>A: Structured answer and selected citation IDs
    A->>A: Validate citation policy and resolve current evidence
    A-->>F: Answer, used versions, deduplicated sources
    F-->>U: Complete answer and expandable evidence cards
```

If the API explicitly constrains `obds_version`, every version-aware tool call is
forced to that version. Otherwise, model tool arguments can select a version;
missing selection resolves to the newest available XSD version.

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
