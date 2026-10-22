请使用仓库中的 $powercontext-contest-workflow skill 完成赛题。根据它的路由表选择适用技能，并在应用前读取对应的 SKILL.md。

赛题：<编号和名称>
题目文件：docs/powercontext-contest/<对应题目文件>.md

先按 Skill 熟悉项目和赛题约束，检查仓库说明、当前 Git 状态、赛题基线、相关 RFC、实现与测试；保留已有改动。然后创建隔离的 feat 分支或 worktree，制定方案后，人工审核通过后完成赛题实现，禁止直接开始写代码。

完成后自己 review 完后，再使用 subagent 并结合相关的 review Skill 运行题目要求的验证和相关检查，按题目交付要求整理结果，说明改动、验证证据和提交信息。不要推送、开 PR 或对外提交，除非我明确要求。
