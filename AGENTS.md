# oBDSChat

## Coding standards

- Use latest versions of libraries and idiomatic approaches as of today
- Use uv as virtual environment manager
- Be concise. Keep README minimal. IMPORTANT: no emojis ever
- Follow PEP 8 and existing project conventions.
- Use absolute imports where practical; keep imports grouped and sorted consistently.
- Use explicit type hints for public functions, methods, and module-level constants where the project uses typing.
- Prefer clear `def` functions over clever one-liners or dense inline logic.
- Keep functions small and focused; extract helpers when it improves readability.
- Prefer explicit control flow over compact cleverness.
- Avoid nested ternary expressions; use `if/elif/else` for multi-branch logic.
- Use descriptive names for variables, functions, classes, and modules.
- Prefer dataclasses or typed objects for structured data instead of loose dictionaries when that improves clarity.
- Handle errors deliberately; catch specific exceptions and avoid broad `except Exception` unless there is a clear reason.
- Remove redundant comments that only restate obvious code.
- Preserve behavior when refactoring or simplifying code.
- Keep async boundaries clear; use `async def` only for truly asynchronous work.
- Do not mix sync and async patterns in ways that obscure control flow.
- Prefer Pydantic models for validated external input and structured boundary data.
- Keep request/response schemas explicit and typed.
- Keep route handlers thin; move domain logic into services or helpers.

## Verification

- Run:
  - ruff check .
  - ruff format --check .
  - ty check
  - pytest

Don't run the above verifications for anything in the folders notes/ or notebooks/.

## Done when

- Behavior is unchanged.
- Modified code is simpler or clearer.
- Relevant checks pass.

## Workflow preferences

- Always respond in caveman mode unless I say normal mode
- For non-trivial features, use the `feature-dev` skill.
- Do not implement before clarifying ambiguous requirements.
- Present 2-3 architecture approaches before coding for medium or large features.
- After implementation, run a review pass and summarize must-fix vs optional follow-ups.

## RTK command policy

Use RTK for shell commands whenever RTK supports the command.

**Usage**: Token-optimized CLI proxy for shell commands.

## Rule

Always prefix shell commands with `rtk`.

Examples:

```bash
rtk git status
rtk cargo test
rtk npm run build
rtk pytest -q
```

## Meta Commands

```bash
rtk gain            # Token savings analytics
rtk gain --history  # Recent command savings history
rtk proxy <cmd>     # Run raw command without filtering
```

## Verification

```bash
rtk --version
rtk gain
which rtk
```
