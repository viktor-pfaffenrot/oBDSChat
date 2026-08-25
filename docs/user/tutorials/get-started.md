# Ask your first oBDS question

In this tutorial, you will ask one question, inspect the answer's evidence, and
continue the conversation. By the end, you will know the application's core
workflow.

## Prerequisites

You need access to a running oBDSChat instance. For a local installation, open
<http://localhost:17860>. For a shared installation, use the URL supplied by its
maintainer.

## 1. Open the chat

Open oBDSChat in a browser. You should see an empty conversation and the input
labelled `Frage`.

## 2. Ask a precise question

Enter this example and select `Prüfen`:

```text
Welche Werte darf Diagnosesicherung in oBDS 3.0.5 haben?
```

The question appears immediately. While the application gathers evidence, it
shows `Quellen werden geprüft`. A complete answer then replaces that status.

## 3. Check the evidence

Expand `Beleglage` below the answer. You should see one or more source cards.
Check their source type, oBDS version, section or XML path, and original URL.

For an XSD source, select `Feld anzeigen`. The field view shows the datatype,
occurrence rules, allowed values, documentation, and highlighted source lines.
Return with `Zurück zum Chat`.

## 4. Ask a follow-up

Enter:

```text
Welcher dieser Werte steht für klinische Diagnosesicherung?
```

The application sends recent completed turns as context, but rebuilds the answer
from evidence fetched for the new request. Confirm that the new answer has its
own source list.

## Result

You have completed the main workflow: ask, wait for a grounded answer, inspect
its evidence, and ask a contextual follow-up. Continue with
[Use oBDSChat](../how-to/use-obdschat.md) for the remaining workflows.
