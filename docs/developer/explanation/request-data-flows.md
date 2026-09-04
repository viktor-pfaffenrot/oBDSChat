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

The evidence view shows the declaration with up to three lines of surrounding
context. To keep the response manageable, the excerpt is limited to 160 lines.
The metadata still records the declaration's complete line range and indicates
whether the excerpt omits any part of it.
