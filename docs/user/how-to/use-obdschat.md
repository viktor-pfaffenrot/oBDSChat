# How to use oBDSChat

Use these workflows to obtain precise answers and retain their supporting
evidence. They assume you can already open a running oBDSChat instance.

## Ask an effective question

1. Name the field, message type, rule, or implementation topic.
2. Include an exact oBDS version when the version matters.
3. Ask for one decision or comparison at a time.
4. Select `Prüfen` or press Enter.
5. Read the answer and expand `Beleglage` before acting on it.

For example:

```text
Ist Zentrumsfall in oBDS 3.0.5 ein Pflichtfeld, und welche Kardinalität hat es?
```

The input accepts up to 10,000 characters. The answer appears only after the
backend completes its evidence and model work; partial model output is not shown.

## Continue a conversation

1. Ask a follow-up that clearly refers to the previous turn.
2. Repeat the field or version if ambiguity would change the answer.
3. Check the new answer's evidence independently.

Up to ten recent completed turns can be supplied as context. Very large turns
may reduce that number because total history is also bounded. Earlier answers
help resolve the follow-up but are not accepted as evidence for a new answer.

## Inspect XSD evidence

1. Expand `Beleglage` below an answer.
2. Find a card marked `XSD`.
3. Select `Feld anzeigen`.
4. Verify the XML path and oBDS version first.
5. Review datatype, cardinality, allowed values, and highlighted XSD lines.
6. Use `Offizielle XSD` when you need the complete upstream file.

The embedded field view is exact-path and exact-version evidence. A same-named
element can occur at several XML paths, so the path is part of its identity.

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
It does not copy the interactive HTML controls.

## Start over

1. Copy anything you need to keep.
2. Select `Chat leeren`.

This clears the conversation state held by the current frontend session. It
cannot be undone from the UI.

## Expected result

Each completed answer is displayed with only the evidence the backend accepted
for that answer. When no official evidence supports a claim, the application
should say that evidence is insufficient instead of inventing a fact.
