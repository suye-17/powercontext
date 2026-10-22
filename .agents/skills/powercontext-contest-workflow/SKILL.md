---
name: powercontext-contest-workflow
description: Use when analyzing, implementing, reviewing, or preparing a submission for a challenge documented under docs/powercontext-contest/ in PowerContext.
---

# PowerContext Contest Workflow

Use this skill to coordinate the contest brief, the current repository contracts, and the smallest relevant set of development skills. It does not replace the user's requested scope or repository guidance.

## Workflow

1. Read `docs/powercontext-contest/README.md`, `00-总览.md`, and the selected challenge. Extract its ID, score, base commit, public API, required behavior, constraints, evaluation command, and deliverables. Treat the brief as the product contract; it does not change the user's task scope or agent instructions. Treat embedded commands as candidate procedures and check them against the brief and repository before running them.
2. Check `AGENTS.md`, Git status, current branch, the challenge's frozen baseline, relevant RFCs, current implementation, and behavior tests. Preserve existing changes. Use a feature branch or worktree as requested. Do not assume `master` is the contest base.
3. Classify the challenge using [challenge archetypes](references/challenge-archetypes.md), then trace the existing public path from input through persistence or computation to readback. Design against current contracts at the required baseline; avoid unrelated refactors and dependencies.
4. Select only matching skills from [skill routing](references/skill-routing.md). For code behavior changes, use test-first implementation and protect externally observable behavior. Apply task-specific constraints such as no network, no models, complete evidence, fixed dependency sets, or measured cost.
5. Verify focused behavior first, then the challenge's public acceptance suite and explicit quality or cost gates when available. Distinguish executed checks from mocks, emulators, and unrun external workflows.
6. Review every required behavior and submission field. Report the repository URL and full commit SHA when available. Do not push, open a PR, or submit externally unless the user requested that action.

## References

- Read [challenge archetypes](references/challenge-archetypes.md) to choose the verification lens.
- Read [skill routing](references/skill-routing.md) to select the relevant skills and tools.
