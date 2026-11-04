---
name: Bug report
about: A crash, a wrong verdict, or a plan entry that should not be there
title: ""
labels: bug
assignees: ""
---

**What happened**
A clear description of the bug — the command you ran and what
worktree-janitor reported.

**What you expected**
The verdict, reason, or plan you expected instead, and the git
facts that support it (e.g. the branch was not actually merged).

**The repository state**
If you can, paste the output of `git worktree list --porcelain`
and `git branch --format='%(refname:short) %(objectname)'` from
the repo, plus the scan (or `--json`) output. A minimal fixture
built like `examples/make_sample.py` that reproduces the problem
is just as good. Feel free to rename paths and branches.

**Environment**
- worktree-janitor version (the report header prints it):
- Python version:
- git version:
- OS:

**Output**
Paste the full text report, or the `--json` output if the bug is
in the schema.
