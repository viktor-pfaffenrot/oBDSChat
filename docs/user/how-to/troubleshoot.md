# How to troubleshoot user problems

This guide covers problems visible in the browser. It assumes you do not manage
the running services.

## No question is submitted

1. Remove leading or trailing whitespace.
2. Enter at least one non-whitespace character.
3. Shorten input to 10,000 characters or fewer.
4. Select `Prüfen` again.

## The answer takes a long time

1. Keep the tab open while `Quellen werden geprüft` is visible.
2. Avoid submitting the same question repeatedly.
3. Wait for either a complete answer or an error card.
4. If requests repeatedly fail, send the maintainer the time, question topic,
   and exact error text. Do not send secret values.

The app allows potentially long waiting times because one request can include source
search and final answer validation. A request can also wait briefly when all query slots configured by the maintainer are active.

## `Backend ist derzeit nicht erreichbar.`

1. Reload the page once.
2. Confirm the application URL is correct.
3. Check whether coworkers see the same problem.
4. Send the maintainer the time and frontend URL.

## `Backend hat eine ungültige Antwort geliefert.`

1. Copy the conversation if possible.
2. Reload the page and retry once.
3. Report the exact question, time, and error.

The app received data that did not match its expected response.

## The requested version or field is unavailable

1. Check version spelling, including all three numeric parts such as `3.0.5`.
2. Check the complete XML path shown in prior evidence.
3. Ask the chatbot which versions are available, or retry without constraining a version.
4. If a known official version remains unavailable, report it to the maintainer;
   synchronized source data may be stale or incomplete.

## An answer has no sources

Treat the answer as an explicit statement of insufficient evidence, not as an
oBDS fact. Rephrase with a precise field, XML path, topic, or version. Do not use
an unsupported claim.

## Copy does not reach the clipboard

1. Allow clipboard access if the browser asks.
2. Retry `Chat kopieren` after the current answer completes.
3. If browser policy blocks clipboard access, select and copy visible text
   manually.

## A source link does not open

1. Allow the browser to open the public source site.
2. For XSD evidence, try `Offizielle XSD` from the field view.
3. Report persistent dead upstream links with the source title and version.
