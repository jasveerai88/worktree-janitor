# worktree-janitor

**Parallel agents leave worktrees behind like coat
hangers. This is the inventory that tells you which ones
you can actually throw away.**

Every AI coding session that runs in its own git
worktree leaves the worktree behind when it ends —
headless runs leave theirs *locked*. A forgotten
worktree pins its branch, holds a folder of disk, and
may contain the only copy of some piece of work. So the
pile grows: nobody deletes blindly (that loses work),
nobody keeps track (that was the manual part), and
`git worktree list` gets longer every week.

`worktree-janitor` makes the weekly ritual one read-only
pass. It asks git — and only git — for the facts about
every worktree and local branch: dirty or clean,
uncommitted and untracked file counts, merged into the
default branch or not, unpushed commits, last-commit
age, locked and prunable state. Each worktree gets a
verdict with its reasons printed. Then the `plan`
subcommand prints the exact cleanup commands — and
never runs them.

- Pure Python 3 standard library — no dependencies, no install step
- Local only — nothing leaves your machine; the tool runs git's read-only queries and nothing else
- Provably read-only: every git call funnels through an allowlist gate in the code (see below), and all calls run with `GIT_OPTIONAL_LOCKS=0`, so git does not even refresh the index
- Script-friendly exit codes: `0` = nothing to clean, `1` = cleanup candidates or REVIEW items exist, `2` = error

## The read-only promise, enforced

Plenty of tools *say* read-only. This one is built so
the mutating path does not exist:

- The only git commands ever invoked are
  `worktree list --porcelain`, `status --porcelain=v1
  --branch`, `branch --format / --merged`,
  `log` (tip facts and range counts), and `stash list`.
- Every invocation passes through one function that
  refuses — before git starts — any argv containing a
  mutating verb or flag (`remove`, `prune`, `checkout`,
  `clean`, `-d`, `--force`, ...). The test suite records
  every argv of a full scan + plan run and asserts each
  one passes the gate.
- The cleanup commands in a `plan` are output *text*,
  assembled as strings. There is no code path from the
  plan to a shell. The suite also snapshots the whole
  fixture repo (git's listings + every working-tree
  file) around a scan and a plan and requires exact
  equality.

## Verdicts

- **SAFE_TO_REMOVE** — clean, branch merged into the
  default branch, no unpushed commits, not locked, not
  the main worktree, not the worktree the scan was run
  from. A prunable registration (directory already gone)
  is also SAFE: clearing it cannot lose files.
- **REVIEW** — something in it exists nowhere else, or
  cannot be verified: uncommitted changes, untracked
  files, an unmerged branch, unpushed commits, a
  detached HEAD, an unreadable status. The exact reason
  is always printed. REVIEW worktrees never appear in
  the plan.
- **KEEP** — the main worktree, a worktree git reports
  as locked, the worktree the scan was run from, or a
  bare repository.

Branches get their own inventory: merged or not, tip
age, checked out where, upstream and unpushed count. A
branch that is merged, not checked out anywhere, and
whose last commit is at least `--stale-days` old
(default 30) is **STALE** and joins the plan as
`git branch -d` — lowercase, so git itself refuses if
the branch somehow is not merged. The plan never prints
`-D`. One sequencing subtlety is handled: a merged, old
branch checked out *only* in a dead (prunable)
registration is flagged `stale_after_prune`, and its
`git branch -d` is sequenced after the
`git worktree prune` that frees it.

## What "unpushed" means here

With an upstream configured, unpushed = commits in
`upstream..branch`, from git's own record. With no
upstream, the count is taken against the default branch
(`default..branch`) — and the field names its basis, in
text and in JSON (`unpushed_basis`). If neither is
possible, the count is `null`, the check is named under
CHECKS SKIPPED, and the worktree is REVIEW, never SAFE.
A check that cannot be made is reported, never silently
passed.

Ages are computed from git's committer timestamps
against the moment the scan ran, and dates print in
UTC. Nothing is estimated and nothing is invented:
every number in the report is git's own.

## Usage

```bash
# 0. Build the bundled sample sprawl (or use your own repo)
python3 examples/make_sample.py /tmp/worktree-janitor-demo

# 1. Inventory + verdicts (DIR = any folder in the repo)
python3 src/worktree_janitor.py scan /tmp/worktree-janitor-demo/repo

# 2. The cleanup plan — printed, never executed
python3 src/worktree_janitor.py plan /tmp/worktree-janitor-demo/repo

# 3. Drive it from a script or an agent
python3 src/worktree_janitor.py scan REPO --json

# 4. Tune staleness (default 30 days)
python3 src/worktree_janitor.py scan REPO --stale-days 90
```

Running with no subcommand is `scan` on the current
folder.

## Use with AI agents

Every run takes `--json`: one JSON object with the same
facts and exit codes as the text view (errors come back
as `{"error": ...}`, argparse usage errors included):

```bash
python3 src/worktree_janitor.py scan REPO --json
python3 src/worktree_janitor.py plan REPO --json
```

Agents: read [SKILL.md](SKILL.md) first — the verdict
rules at their exact edges, the JSON schema, exit codes,
and the honesty rules (a plan is a *suggestion computed
from git's facts*; running the commands is a human's
decision, and worktree-janitor will never do it for
you).

## Real run (verbatim)

Building the bundled sample and scanning it. The full
captured outputs also live in
[examples/](examples/sample-report.txt):

```text
$ python3 examples/make_sample.py /tmp/worktree-janitor-demo
Created /tmp/worktree-janitor-demo/repo plus worktrees wt-safe, wt-dirty, wt-locked, wt-detached under /tmp/worktree-janitor-demo (wt-gone's folder was deleted on purpose — its registration is prunable)
(exit code: 0)

$ python3 src/worktree_janitor.py scan /tmp/worktree-janitor-demo/repo
worktree-janitor v0.1.0 — scan · repo repo
/tmp/worktree-janitor-demo/repo · default branch main (the main worktree's checked-out branch) · stale threshold 30 day(s)
6 worktree(s) — 2 KEEP · 2 SAFE_TO_REMOVE · 2 REVIEW · 8 local branch(es) · 1 stale · 1 stash entry

WORKTREES:
  [REVIEW] /tmp/worktree-janitor-demo/wt-detached — detached @ a667b51
      clean · last commit 0 day(s) ago, 2026-11-04 (UTC) — "merge done"
      why: detached HEAD — no branch holds this worktree's position, so its merge and unpushed state cannot be verified
  [REVIEW] /tmp/worktree-janitor-demo/wt-dirty — branch feat-wip @ c602462
      1 uncommitted change(s), 1 untracked file(s) · last commit 0 day(s) ago, 2026-11-04 (UTC) — "wip work"
      why: 1 uncommitted change(s) (staged 0, unstaged 1) — they exist only in this worktree; 1 untracked file(s) — they exist only in this worktree; branch feat-wip is not merged into main (1 commit(s) not in main); 1 unpushed commit(s) (no upstream configured — counted against the default branch main)
  [SAFE_TO_REMOVE] /tmp/worktree-janitor-demo/wt-gone — branch feat-gone @ a667b51
      directory gone (prunable)
      why: the directory is gone and git marks this worktree prunable (gitdir file points to non-existent location) — clearing the registration cannot lose files; its branch, if any, stays in the branch list below
  [SAFE_TO_REMOVE] /tmp/worktree-janitor-demo/wt-safe — branch feat-done @ f172613
      clean · last commit 0 day(s) ago, 2026-11-04 (UTC) — "done work"
      why: clean, branch feat-done merged into main, no unpushed commits, not locked
  [KEEP] /tmp/worktree-janitor-demo/repo — branch main @ a667b51
      clean · last commit 0 day(s) ago, 2026-11-04 (UTC) — "merge done"
      why: the main worktree
  [KEEP] /tmp/worktree-janitor-demo/wt-locked — branch feat-locked @ a667b51
      clean · last commit 0 day(s) ago, 2026-11-04 (UTC) — "merge done"
      why: locked by git — reason recorded by git: agent session running

BRANCHES:
  feat-done · checked out at /tmp/worktree-janitor-demo/wt-safe · merged into main · last commit 0 day(s) ago, 2026-11-04 (UTC) — "done work"
  feat-gone · checked out at /tmp/worktree-janitor-demo/wt-gone · merged into main · last commit 0 day(s) ago, 2026-11-04 (UTC) — "merge done"
  feat-locked · checked out at /tmp/worktree-janitor-demo/wt-locked · merged into main · last commit 0 day(s) ago, 2026-11-04 (UTC) — "merge done"
  feat-merged-new · merged into main · last commit 0 day(s) ago, 2026-11-04 (UTC) — "new work"
  feat-merged-old · merged into main · last commit 2498 day(s) ago, 2020-01-02 (UTC) — "old work" · STALE
  feat-unmerged · NOT merged into main (1 commit(s) not in it) · last commit 0 day(s) ago, 2026-11-04 (UTC) — "unmerged work"
  feat-wip · checked out at /tmp/worktree-janitor-demo/wt-dirty · NOT merged into main (1 commit(s) not in it) · last commit 0 day(s) ago, 2026-11-04 (UTC) — "wip work"
  main · default · checked out at /tmp/worktree-janitor-demo/repo · merged into main · last commit 0 day(s) ago, 2026-11-04 (UTC) — "merge done"

STASH: 1 entry — the stash is shared across all worktrees; removing a worktree never touches it, and neither does this tool:
  stash@{0}: On main: test stash

VERDICT: cleanup candidates exist — run the `plan` subcommand for the exact commands, review the REVIEW items by hand first.
worktree-janitor only reads: every fact above came from git's own read-only queries, nothing was executed, removed or changed — not even an index refresh (GIT_OPTIONAL_LOCKS=0). Verdicts are git's facts (clean / merged / unpushed / locked), never a guess; a check that could not be made is named under CHECKS SKIPPED and its worktree is REVIEW, never SAFE.
(exit code: 1)
```

The plan for the same repo — printed, and the sample
repo is still standing afterwards, untouched:

```text
$ python3 src/worktree_janitor.py plan /tmp/worktree-janitor-demo/repo
worktree-janitor v0.1.0 — plan · repo repo
/tmp/worktree-janitor-demo/repo · default branch main (the main worktree's checked-out branch) · stale threshold 30 day(s)
6 worktree(s) — 2 KEEP · 2 SAFE_TO_REMOVE · 2 REVIEW · 8 local branch(es) · 1 stale · 1 stash entry

CLEANUP PLAN — the exact commands, in order. worktree-janitor does NOT run them; copy them yourself after reading the REVIEW items:

  git worktree remove /tmp/worktree-janitor-demo/wt-safe
      # clean, branch feat-done merged into main, no unpushed commits, not locked
  git worktree prune
      # clears every prunable worktree registration whose directory is already gone
  git branch -d feat-merged-old
      # merged into main, last commit at least 30 day(s) old, not checked out anywhere — lowercase -d, so git itself refuses if the branch is somehow unmerged

After running them, re-run `python3 src/worktree_janitor.py scan` to confirm the repo is CLEAN. Branch deletions use lowercase -d: git itself refuses if a branch is somehow unmerged. There is no -D anywhere in this plan, deliberately.

NOT IN THE PLAN: 2 worktree(s) marked REVIEW — each holds something that exists nowhere else. See the scan report.

worktree-janitor only reads: this plan was computed from git's own read-only queries and printed as text. Nothing was executed, removed or changed.
(exit code: 1)
```

And the two clean failures — not a repo, in text and in
`--json` (never a traceback):

```text
$ python3 src/worktree_janitor.py scan /tmp/wtj-plain
ERROR: could not read the worktree list (is this a git repository?): fatal: not a git repository (or any parent up to mount point /)
(exit code: 2)

$ python3 src/worktree_janitor.py scan /tmp/wtj-plain --json
{"error": "could not read the worktree list (is this a git repository?): fatal: not a git repository (or any parent up to mount point /)"}
(exit code: 2)
```

## Tests

```bash
python3 -m unittest discover -s tests -v
# Ran 36 tests — OK
```

The suite builds REAL git repositories with the git CLI
in temp dirs — several worktrees, dirty files, merged
and unmerged branches, a locked worktree, a deleted
(prunable) one, a stash, and a second fixture with a
real local upstream — and asserts the verdicts that were
worked out by hand in the test-suite header (6 worktrees
= 2 KEEP + 2 SAFE + 2 REVIEW; exactly 1 stale branch;
the plan is exactly 3 commands). Both parsers are pinned
on synthetic porcelain (locked/prunable reasons with
spaces, dotted and slashed branch names, ahead/behind
headers, detached and unborn states). Edge repos behave:
empty (no commits), bare, not-a-repo and git-missing
all end in clean exits, never tracebacks. And the
read-only promise is proven two ways — every subprocess
argv of a scan + plan is recorded and checked against
the allowlist, and the fixture's full state (git
listings + every working-tree file) is identical before
and after.

## Two jobs this tool does

1. **For any developer running parallel AI agents**
   (the public GitHub Sponsors surface): the weekly
   worktree ritual from every worktree guide — list,
   then check each folder by hand for uncommitted work,
   merged status, unpushed commits — becomes one
   deterministic command whose verdicts an agent can
   also consume via `--json` before it proposes any
   cleanup.
2. **For its author** (internal): the author's engine
   runs parallel agent lanes and clones/audits many
   repos; this is the safe pre-cleanup inventory for
   that sprawl — report first, decide with the facts in
   hand, delete by hand only what the report cleared.

## Known limits

- **Git-visible state only.** Tools that keep their own
  worktree stashes outside the repo (some editors and
  agent runners do) are invisible to git and therefore
  to this tool. Orphaned directories that git no longer
  has any registration for are likewise out of scope —
  v1 inventories what git itself records.
- **No disk sizes.** A worktree's on-disk weight
  (especially `node_modules`) is not measured in
  v0.1.0; the report tells you what is safe to remove,
  not what each removal reclaims.
- **Local branches only.** Remote-tracking branches are
  read only as upstreams; the inventory and the plan
  concern local worktrees and local branches.
- **The default branch is the main worktree's branch.**
  If the main worktree is detached, the tool falls back
  to a local branch named `main`, then `master`, and
  says which basis it used; if none exists, merge and
  unpushed checks are skipped — named in the report —
  and affected worktrees are REVIEW, never SAFE.
- **A plan is advice, not a transaction.** Facts are
  gathered in one pass; a worktree can change the
  second after the scan. Re-run `scan` after executing
  a plan, and eyeball REVIEW items yourself — that is
  what they are for.

## Privacy

What it touches: the git metadata and working-tree
status of the one repository you name (via git's own
read-only queries).
What it sends: nothing — there is no network code in
this tool, and the git commands it runs perform no
network operations (no fetch, ever).
What it changes: nothing — see "The read-only promise,
enforced" above.

## Support

If this saves you one worktree you were about to delete
— or one afternoon of manual checking — consider
sponsoring: **https://github.com/sponsors/jasveerai88**

## Licence

MIT — see [LICENSE](LICENSE).
