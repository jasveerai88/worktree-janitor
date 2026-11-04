# examples — what these files are

`make_sample.py` builds a small, fully local sample
repo with the worktree sprawl parallel agents leave
behind (see its docstring for the layout). The other
three files are **captured output** from running
worktree-janitor on that fixture — real runs, not
mock-ups:

- `sample-report.txt` — `scan` (text), captured
  2026-11-04 at `/tmp/worktree-janitor-demo`. Exit 1.
- `sample-plan.txt` — `plan` (text), same fixture, same
  day. Exit 1. The commands were printed, not run — the
  sample repo was still standing, untouched, afterwards.
- `sample-report.json` — `scan --json`, same fixture.
  Pure JSON (no header lines) so it stays machine-usable
  as a schema example. Its `now_unix` differs from any
  re-run by definition — ages are computed against the
  moment of the run.

Paths and commit hashes in the captures are from the
capture machine and will differ on yours. To regenerate:

```bash
python3 examples/make_sample.py /tmp/worktree-janitor-demo
python3 src/worktree_janitor.py scan /tmp/worktree-janitor-demo/repo
python3 src/worktree_janitor.py plan /tmp/worktree-janitor-demo/repo
python3 src/worktree_janitor.py scan /tmp/worktree-janitor-demo/repo --json
```

Expected shape, worked out by hand (and asserted by the
test suite): 6 worktrees = 2 KEEP (main + locked) +
2 SAFE_TO_REMOVE (clean/merged + prunable) + 2 REVIEW
(dirty/unmerged + detached); 8 local branches; exactly
1 stale branch (`feat-merged-old`, tip dated
2020-01-02); 1 stash entry; the plan is exactly 3
commands (`git worktree remove`, `git worktree prune`,
`git branch -d feat-merged-old`).
