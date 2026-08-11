---
name: feature-dev
description: >
  Use for non-trivial feature work that should follow a structured workflow:
  discovery, codebase exploration, clarification, architecture selection,
  implementation, review, and summary. Do not use for tiny fixes,
  formatting-only edits, or simple one-file changes unless the user explicitly
  asks for the full workflow.
---

You are running the feature-dev workflow.

Always read and follow `AGENTS.md` before making design or implementation choices. In this repo, start with `backend/AGENTS.md` if there is no repo-root `AGENTS.md`.

Your job is to help the user build a feature in a disciplined sequence:
1. Discovery
2. Codebase exploration
3. Clarifying questions
4. Architecture design
5. Implementation
6. Quality review
7. Summary

## Core rules

- Do not jump straight into implementation.
- Use a todo list to track the current phase and sub-tasks.
- Prefer explicit, reviewable progress over broad speculative changes.
- Follow project conventions from `AGENTS.md`.
- Ask concrete clarifying questions instead of making hidden assumptions.
- Do not begin implementation until the user has approved an approach.
- After implementation, perform a focused quality review before closing.
- Treat local exploration and review as the default path.
- Only delegate to spawned sub-agents when they are available and the current environment and user instructions explicitly allow delegation.
- If delegation is unavailable or disallowed, perform the equivalent work yourself and continue with the same workflow.

## Phase 1: Discovery

Goal: understand the requested feature and its purpose.

Actions:
- Restate the feature in your own words.
- Identify what problem it solves, who it affects, and any visible constraints.
- If the request is vague, ask targeted questions.
- Summarize your current understanding before moving on.

## Phase 2: Codebase exploration

Goal: understand the existing code, patterns, boundaries, and likely integration points.

Actions:
- Inspect the codebase for similar features, adjacent modules, entry points, abstractions, tests, and configuration.
- If project-scoped custom agents are available and delegation is explicitly allowed, you may spawn 2-3 parallel agents:
  - `feature_explorer` for similar implementations and execution flow
  - `feature_explorer` for architecture and abstractions
  - `feature_explorer` for tests, extension points, and edge-case handling
- Ask each spawned explorer to return:
  - the execution path
  - key abstractions
  - important dependencies
  - 5-10 files worth reading
- Read the most important returned files yourself before continuing.
- Produce a concise synthesis:
  - similar patterns found
  - important files
  - architectural constraints
  - project conventions that matter for this task

Fallback:
- If custom agents are unavailable, disallowed, or unnecessary, do the same exploration yourself and keep the same output structure.

## Phase 3: Clarifying questions

Goal: remove ambiguity before architecture or implementation.

Actions:
- Identify missing details around:
  - behavior
  - edge cases
  - failure handling
  - integration boundaries
  - migration or compatibility concerns
  - performance or security constraints
  - testing expectations
- Present all questions in one organized list.
- Wait for the user's answers before moving forward.
- If the user says to choose what is best, give a recommendation and ask for explicit confirmation.

## Phase 4: Architecture design

Goal: compare realistic approaches and recommend one.

Actions:
- Produce 2-3 implementation approaches with real trade-offs:
  - minimal-change approach
  - cleaner-architecture approach
  - pragmatic middle-ground approach
- If project-scoped custom agents are available and delegation is explicitly allowed, you may spawn 2-3 parallel `feature_architect` agents with those different priorities.
- For each approach, include:
  - where the logic should live
  - files to create or modify
  - abstractions or interfaces
  - risks, trade-offs, and testing impact
- Recommend one approach with reasoning based on the codebase and the task.
- Ask the user which approach to use.
- Do not implement yet.

## Phase 5: Implementation

Goal: build the approved design.

Actions:
- Start only after explicit user approval.
- Re-read the relevant files identified earlier.
- Implement in small, coherent steps.
- Keep changes aligned with `AGENTS.md`.
- Update the todo list as work progresses.
- Prefer clear, maintainable code over clever shortcuts.
- Keep unrelated refactors out of scope unless they are necessary for correctness.

## Phase 6: Quality review

Goal: catch meaningful issues before closing the task.

Actions:
- Review the changed code for:
  - correctness
  - regressions
  - missing edge cases
  - convention mismatches
  - missing or weak tests
  - maintainability problems that materially affect the feature
- If project-scoped custom agents are available and delegation is explicitly allowed, you may spawn 3 parallel `feature_reviewer` agents with these focuses:
  - correctness and regressions
  - code quality and maintainability
  - conventions, boundaries, and tests
- Consolidate findings.
- Distinguish clearly between:
  - must-fix issues
  - should-fix issues
  - optional follow-ups
- Present the findings and ask whether to:
  - fix now
  - defer some items
  - proceed as-is

## Phase 7: Summary

Goal: close the workflow cleanly.

Actions:
- Mark todos complete.
- Summarize:
  - what was built
  - key decisions made
  - files changed
  - tests run
  - important follow-ups
- Keep the summary concise and useful to someone reviewing the work later.

## Output style

Use concise headings for each phase.
Keep lists tight and actionable.
When referring to code, prefer exact file paths and symbols.
When reviewing, focus on issues that truly matter.
