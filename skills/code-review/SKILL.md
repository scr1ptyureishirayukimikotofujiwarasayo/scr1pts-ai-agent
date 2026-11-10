---
name: code-review
description: Review a diff or file for correctness, security and maintainability. Use when the user asks for a review or asks "is this right?".
---

# Code review

## 1. Get the code under review

- Diff: `git diff`, `git diff --staged`, or `git show <sha>`
- Whole files: `read_file` (read the whole thing before judging it)
- Context: `grep` for the symbols being changed, so you know how callers use them.

## 2. Look for, in this order

1. **Correctness** — off-by-one, None/undefined handling, wrong branch, async races,
   resource leaks, error paths that never run.
2. **Security** — untrusted input reaching a shell/SQL/eval/path, secrets in code,
   missing auth checks, unsafe deserialisation, injection into HTML.
3. **Data loss** — writes without validation, migrations that drop columns,
   destructive commands without a backup.
4. **Performance** — N+1 queries, work inside loops that could be hoisted,
   unbounded growth.
5. **Maintainability** — dead code, duplicated logic, misleading names,
   tests that assert nothing.

## 3. Verify instead of guessing

Run the tests, the type checker or a small repro before claiming a bug exists.
If a claim is a guess, label it as one.

## 4. Report format

For every finding:

```
<severity: blocker|major|minor|nit> <file>:<line>
Problem: what is wrong and why it matters.
Fix: the smallest change that resolves it.
```

End with a short summary: what is solid, what must change before merge,
and the top 3 things to fix if time is short. Never invent line numbers.
