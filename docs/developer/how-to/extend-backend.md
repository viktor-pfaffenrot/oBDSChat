# How to extend the backend

Use the matching procedure below. Keep FastAPI handlers thin and place retrieval
or orchestration logic in focused modules.

## Add or change an HTTP route

1. Define explicit request and response Pydantic models in `src/backend/app.py`
   or a focused boundary module if the route family becomes large.
2. For external input, set deliberate length/range constraints and decide whether
   unknown fields must be forbidden.
3. Add a thin route that calls domain functions and converts known exceptions to
   intentional HTTP responses.
4. Do not expose raw model-provider, database, secret-path, or parser exceptions.
5. Add boundary tests in `tests/test_app.py` for success, validation, and each
   mapped failure.
6. Inspect `/openapi.json`; treat it as the canonical signature.
7. If the frontend consumes the route, update `src/frontend/api.py` models and
   client method, then add frontend boundary tests.

Expected result: the route contract is visible in generated OpenAPI, errors are
stable, and no domain work is embedded in the handler.

## Add a model-callable evidence tool

1. Implement a typed domain function in the owning module. Keep arbitrary SQL,
   filesystem paths, and external URLs out of model-controlled arguments.
2. Add an adapter in `src/backend/tools.py` that returns JSON-serializable typed
   data.
3. Define one `LocalTool` with a unique name, precise description, and strict
   object schema.
4. Include every property in `required`. Use a union containing `null` when a
   value is semantically optional.
5. Register the tool in `TOOLS`.
6. If results can support public claims, include stable source metadata that
   `_citation_id` can convert into an ID. Extend citation-ID and public-source
   conversion deliberately for a new source type.
7. Test schema strictness, argument validation, handler output, tool execution,
   and citation selection.

Expected result: the first model round can select the new tool, outputs are
bounded and serializable, and cited results can be proven to come from the current
request.

## Add a schema-derived fact

1. Add the fact to the relevant frozen Pydantic model in `src/backend/xsd.py`.
2. Extract it while building `_SchemaIndex`; do not reparse the schema for every
   request.
3. Add it to a focused catalog query or tool result.
4. If it belongs in exact evidence, update the backend evidence response and the
   mirrored frontend model.
5. Update XSD fixtures and tests for direct, inherited, missing, and malformed
   cases where relevant.
6. Clear the schema catalog cache in tests that change configured XSD files.

Expected result: the fact is deterministic for an exact version/path and remains
traceable to the official XSD.

## Add a model provider

1. Add a value to `LlmProvider` in `src/backend/config.py`.
2. Add provider base URL, route policy, and key resolution in
   `Settings.resolve_llm_endpoint`.
3. Keep `backend.llm` provider-neutral unless the Chat Completions protocol itself
   differs.
4. Add settings tests for normalization, missing key, mounted key, and endpoint
   resolution.
5. Update `.env.example` without adding real credentials.

Expected result: `answer_question` receives one resolved `LlmEndpoint` and does
not branch on provider.

## Review checklist

- Public inputs and outputs are typed.
- Error mapping is specific and tested.
- Tool results are bounded and JSON-serializable.
- Source-bearing claims can pass current-request citation validation.
- Frontend boundary models match changed HTTP responses.
- [Test changes](test-changes.md) passes.
