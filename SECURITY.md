# Security

## worktree-janitor's own posture

worktree-janitor is a safety tool, and its own attack
surface is deliberately minimal:

- **It cannot delete anything.** Every git invocation
  funnels through one gate in the source that refuses —
  before git starts — any argv carrying a mutating verb
  or flag (`remove`, `prune`, `checkout`, `clean`, `-d`,
  `--force`, …). The only commands ever run are git's
  read-only queries: `worktree list`, `status`,
  `branch`, `log`, `stash list`. All calls run with
  `GIT_OPTIONAL_LOCKS=0`, so git does not even refresh
  the index.
- **The plan is inert text.** The cleanup commands a
  `plan` prints are assembled as strings. There is no
  code path from the plan to a shell — no `--execute`
  flag exists or will be added.
- **No network code.** The tool performs no network
  operations, and neither do the git commands it runs
  (no fetch, ever). Freshness of upstream facts is the
  user's own `git fetch` decision.
- **No dependencies.** Python 3.10+ standard library
  only — there is no supply chain beyond Python and
  the git binary already on your machine.

The test suite proves the read-only claim two ways:
every subprocess argv of a full scan + plan run is
recorded and asserted against the allowlist, and the
fixture repo's full state (git listings + every
working-tree file) is byte-identical before and after.

## Reporting a problem

If you find a way to make worktree-janitor execute a
command — from the plan, from a crafted repository, or
from any input — or to write, move, or delete anything
at all, please report it privately through GitHub's
"Report a vulnerability" flow on this repository
(Security → Advisories), rather than a public issue.
A minimal fixture (the bundled `examples/make_sample.py`
shows the shape) that reproduces the problem is the
most helpful thing you can include.

If worktree-janitor *misjudges* a repository — a
worktree marked SAFE_TO_REMOVE that should not be, or
a REVIEW reason that is wrong — that is a correctness
bug, and the dangerous direction matters most: open a
regular issue with the scan output and the git facts
that contradict it.

## Supported versions

Only the latest release is supported. Fixes land in a
new release, never as silent edits to an existing one.
