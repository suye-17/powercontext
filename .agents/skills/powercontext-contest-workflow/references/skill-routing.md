# Skill and Tool Routing

Choose by task stage and observable need. This list records skills visible in the 2026-10-22 session; the active skill catalog may change, so re-check it when using this workflow. Do not load every listed skill for every contest task.

## Contest workflow routing

| Stage or condition | Use |
| --- | --- |
| Start a task in Codex | `superpowers:using-superpowers` |
| New behavior, public contract, or non-obvious implementation choice | `superpowers:brainstorming` |
| Multi-step implementation with a spec or requirements | `superpowers:writing-plans`; use `superpowers:executing-plans` when executing an approved plan inline |
| Isolated implementation requested or needed | `superpowers:using-git-worktrees`; create a normal feature branch when that is the user's selected isolation |
| Behavior change or bug fix | `superpowers:test-driven-development`; use `python-testing-patterns` for pytest-specific fixtures and coverage design |
| Independent tasks can proceed without shared state | `superpowers:dispatching-parallel-agents`; use `superpowers:subagent-driven-development` when executing an approved implementation plan with agents |
| A failure or unexpected result appears | `superpowers:systematic-debugging`; use `diagnosing-superpowers` only when the Superpowers process itself caused trouble |
| Async/concurrency design | `async-python-patterns` |
| A measured performance bottleneck or explicit cost gate | `python-performance-optimization` |
| Before claiming completion | `superpowers:verification-before-completion` |
| Major change or pre-merge review | `superpowers:requesting-code-review`; use `superpowers:receiving-code-review` before acting on review feedback |
| Verified branch is ready for integration | `superpowers:finishing-a-development-branch` |
| Creating or updating a skill | `skill-creator` for scope and structure; `superpowers:writing-skills` for skill authoring and validation |

## All skills visible in this session

### Core workflow and skill management

- `superpowers:using-superpowers` — initialize the relevant skill workflow at the start of a task.
- `superpowers:brainstorming` — clarify and design new behavior before implementation.
- `superpowers:writing-plans` — turn a multi-step specification into an implementation plan.
- `superpowers:executing-plans` — execute an implementation plan in the current session.
- `superpowers:using-git-worktrees` — isolate feature work in a worktree.
- `superpowers:test-driven-development` — write behavior tests before code changes.
- `superpowers:dispatching-parallel-agents` — split independent work across agents.
- `superpowers:subagent-driven-development` — implement an approved plan using subagents.
- `superpowers:systematic-debugging` — find the root cause of a failure before changing code.
- `superpowers:verification-before-completion` — verify evidence before reporting success.
- `superpowers:requesting-code-review` — request review for completed or substantial changes.
- `superpowers:receiving-code-review` — evaluate review feedback technically before applying it.
- `superpowers:finishing-a-development-branch` — decide how to integrate completed branch work.
- `superpowers:diagnosing-superpowers` — investigate repeated process failures in a Superpowers session.
- `skill-creator` — create or update a scoped skill and supporting resources.
- `superpowers:writing-skills` — write and validate skill instructions.
- `find-skills` — discover skills when a capability is missing from the visible catalog.
- `skill-installer` — install a curated skill or a skill from a repository.
- `plugin-management:plugin-management` — inspect or manage plugins, app permissions, and external connections.

### Python development

- `async-python-patterns` — async APIs, task lifecycles, cancellation, and concurrent I/O.
- `python-testing-patterns` — pytest strategy, fixtures, mocking, and test organization.
- `python-performance-optimization` — profiling and measured CPU or memory optimization.

## Tools are not skills

Use local Git for local status, branching, commits, and diffs. The GitHub app is a separate tool set for remote repository metadata, issues, pull requests, reviews, and authorized remote changes. Use it when the task requires remote state or the user explicitly requests it; it does not replace local verification. Superpowers is also a plugin that supplies the `superpowers:*` skills above.
