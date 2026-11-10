---
name: git-commit
description: Write a clean commit message and commit only the intended files. Use when the user says "commit this".
---

# Commit workflow

1. `run_command("git status --short")` and `run_command("git diff --stat")`.
2. Read the real diff before summarising it: `git diff` (or `git diff --staged`).
3. Stage deliberately — `git add <paths>`, never `git add -A` if unrelated files
   are modified.
4. Never commit `.env`, keys, build output or `sessions/`.
5. Message format:

   ```
   <type>: <imperative summary, <= 72 chars>

   Why this change is needed, in one or two sentences. Mention user-visible
   behaviour, not the mechanics.
   ```

   Types: feat, fix, refactor, docs, test, chore, perf.

6. `git commit -m "..."` (multi-line: repeat `-m`), then `git log -1 --stat`.
7. Report the commit hash and anything left unstaged.
