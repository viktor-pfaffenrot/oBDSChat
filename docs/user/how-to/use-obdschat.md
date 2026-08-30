# How to use oBDSChat

Use these workflows to obtain precise answers and inspect their supporting
evidence. The workflows assume you can already open a running oBDSChat instance.

## Ask an effective question

1. Name the field, message type, rule, or implementation topic.
2. Include an exact oBDS version when the version matters.
3. Ask for one decision or comparison at a time.
4. Select `Prüfen` or press Enter.
5. Read the answer and expand `Beleglage` before acting on it.

For example:

```text
Ist Zentrumsfall in oBDS 3.0.5 ein Pflichtfeld?
```

The input accepts up to 10,000 characters. The answer appears only after the
model completes its source-gathering and answer synthesization; partial model output is not shown.

## Continue a conversation

1. Ask a follow-up. The application sends the recent complete conversation as context to the model. Confirm that the new answer has its own source list.
2. Repeat the field or version if ambiguity would change the answer.
3. Check the new answer's evidence independently.

Up to ten recent completed turns can be supplied as context. Very large turns
may reduce that number because total history is also limited.

## Inspect XSD evidence

1. Expand `Beleglage` below an answer.
2. Find a card marked `XSD`.
3. Select `Feld anzeigen`.
4. Verify the XML path and oBDS version first.
5. Review datatype, cardinality, allowed values, and highlighted XSD lines.
6. Use `Offizielle XSD` when you need the complete upstream file.

## Inspect Umsetzungsleitfaden evidence

1. Expand `Beleglage`.
2. Find a card marked `UMSETZUNGSLEITFADEN`.
3. Check the page title and section.
4. Select `Originalseite öffnen` to read its public source page.

## Copy a conversation

1. Wait until any current request completes.
2. Select `Chat kopieren`.
3. Paste into the destination document.

The copied text includes user and chatbot roles plus readable source metadata.

## Start over

1. Copy anything you need to keep.
2. Select `Chat leeren`.

This clears the conversation state. It cannot be undone from the UI.

## Expected result

Each completed answer is displayed with only the evidence the backend accepted
for that answer. When no official evidence supports a claim, the application
should say that evidence is insufficient instead of inventing a fact.
