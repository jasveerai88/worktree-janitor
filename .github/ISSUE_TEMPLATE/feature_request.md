---
name: Verdict or feature request
about: A worktree or branch shape worktree-janitor misjudges, or a read-only fact it should report
title: ""
labels: enhancement
assignees: ""
---

**The gap**
What does the repository look like that worktree-janitor
currently gets wrong or does not see? Name the shape (worktree
state, branch state, git configuration) and what is actually
true about it in git's own records.

**Evidence**
A minimal fixture, the relevant `git worktree list --porcelain` /
`git status` / `git log` output, or a link to the git
documentation the verdict should rest on. Verdicts derive from
git's own records — a request must name the query whose output
proves the fact.

**Suggested verdict**
If you have one: which verdict or reason should change, and in
which direction. Both directions matter — what must be flagged,
and what must *not* be flagged. Note the standing rule: anything
that cannot be verified fails toward REVIEW, never
SAFE_TO_REMOVE.

**Out of scope, so it is said once**
Requests to execute the plan, add an `--execute` flag, delete
anything, or infer safety from heuristics (folder age, name
patterns) will not be taken — the plan being inert text is the
entire design.
