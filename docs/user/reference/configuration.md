# User configuration

oBDSChat currently has no user settings screen. Runtime maintainers select the
model provider, service addresses, ports, source data, and database connection.

## Conversation behavior

| Setting | Current behavior |
| --- | --- |
| Question length | Maximum 10,000 characters |
| Conversation context | At most 10 completed turns |
| Context size | At most 50,000 question-and-answer characters |
| Answer display | Complete answer only; no partial streaming |
| Source display | Evidence used by the answer, deduplicated by source identity |
| Copy format | Plain text with roles and source metadata |

When the context size limit is reached, the frontend keeps the newest complete
turns that fit.

## oBDS version selection

There is no version selector in the current UI. State the version in your
question when it matters:

```text
Welche Kardinalität hat Diagnosesicherung in oBDS 3.0.5?
```

Without an established version, backend tools use the newest synchronized XSD
version. Ask an explicit comparison question when several versions are relevant.

## Evidence sources

| Source type | Content | Opened from UI |
| --- | --- | --- |
| `XSD` | Structure, datatype, cardinality, enumerations, schema documentation | Embedded exact-field view and official XSD |
| `UMSETZUNGSLEITFADEN` | Meaning, guidance, rules, and edge cases | Public original page |

## Session persistence

Conversation state belongs to the active Gradio browser session. The application
does not provide accounts, saved chats, or a conversation history browser.
Use `Chat kopieren` before clearing or closing work you need to retain.
