# Skill and Tool Routing

Follow the current workflow stage and observable need. This list records skills visible in the 2026-10-22 session; the active skill catalog may change, so re-check it when using this workflow. Do not load every listed skill for every contest task. A routed skill must be invoked/read and followed before its stage begins; merely listing its name or writing `$skill-name` in a plan does not mean it has been applied. If a routed skill is unavailable, say so and use the closest available guidance without claiming that skill was used.

## Contest workflow routing

| Workflow phase | Load and apply |
| --- | --- |
| Every new task, before inspecting or acting | `superpowers:using-superpowers`, then this `powercontext-contest-workflow` skill |
| Project/contest inspection | No additional skill by default; follow this skill, `AGENTS.md`, and the challenge contract |
| Design has meaningful ambiguity, new behavior, or a public-contract choice | `superpowers:brainstorming` before settling the approach |
| Every multi-step implementation, after analysis and before coding | `superpowers:writing-plans`; save the plan to `docs/superpowers/plans/YYYY-MM-DD-<challenge-id>-<slug>.md`, present it, and wait for approval |
| User requests or repository policy requires a worktree | `superpowers:using-git-worktrees` before creating the worktree; base new contest work on `contest/base` |
| Approved plan, inline execution | `superpowers:executing-plans` before the first implementation task |
| Approved plan, user-selected/delegated agent execution | `superpowers:subagent-driven-development`; also load `superpowers:dispatching-parallel-agents` only when tasks are independent and can proceed without shared state |
| Behavior change or bug fix | `superpowers:test-driven-development` before changing implementation; add `python-testing-patterns` when pytest fixtures, parametrization, or test structure need design |
| Async/concurrency behavior is part of the change | `async-python-patterns` during design and implementation |
| Measured performance bottleneck or explicit cost gate | `python-performance-optimization` before proposing or measuring optimization |
| Failure or unexpected result during implementation/verification | `superpowers:systematic-debugging` before changing code; `diagnosing-superpowers` only if the Superpowers process itself caused trouble |
| Before claiming completion | `superpowers:verification-before-completion` |
| Substantial change or pre-merge review | `superpowers:requesting-code-review`; load `superpowers:receiving-code-review` before acting on review feedback |
| Verified branch is ready for integration | `superpowers:finishing-a-development-branch` |
| Creating or updating a skill | `skill-creator` for scope/structure and `superpowers:writing-skills` for authoring/validation; read their required supporting references |

### Plan approval stop

`superpowers:writing-plans` is used after repository and challenge analysis. The agent must produce the plan file and expose its link and summary to the user. It must stop there until the user approves the plan or gives corrections. Do not interpret the initial challenge request, time pressure, or a request to “just fix it” as approval of a plan the user has not seen. On approval, load the execution skill selected by the user or the workflow; do not silently skip the plan phase.

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
