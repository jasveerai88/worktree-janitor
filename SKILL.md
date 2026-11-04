---
name: worktree-janitor
description: Read-only inventory of a git repo's worktrees and local branches, with verdicts and a cleanup plan that is printed, never executed. `worktree-janitor scan DIR` (DIR = any folder in the repo) reports per worktree: branch or detached HEAD, dirty/clean with staged/unstaged/untracked counts, ahead/behind vs upstream, merged-into-default or not, unpushed commits (basis always named), last-commit age from git's committer timestamps, locked/prunable state as git reports it. Verdicts: SAFE_TO_REMOVE (clean, merged, no unpushed, not locked, not main, not current — or a prunable registration), REVIEW (uncommitted/untracked work, unmerged branch, unpushed commits, detached HEAD, or any check that could not be verified — exact reason printed), KEEP (main worktree, locked, current worktree, bare). Branches: merged/tip age/checked-out-where/upstream; STALE = merged + not checked out + last commit at least --stale-days old (default 30). `plan` prints the exact git commands for the SAFE items only (worktree remove, one worktree prune, branch -d — never -D; stale-after-prune deletions sequenced after the prune). The tool runs ONLY git's read-only queries (worktree list, status, branch, log, stash list) through an in-code allowlist gate with GIT_OPTIONAL_LOCKS=0, and contains no execution path for the plan. Exit 0 = nothing to clean, 1 = candidates/REVIEW exist, 2 = error (not a repo, git missing). Use before any worktree/branch cleanup in an agent-heavy repo.
---

# worktree-janitor — agent skill

## Purpose

Parallel agent sessions spawn git worktrees constantly
and leave them behind — headless runs leave theirs
locked. The pile is dangerous in both directions:
deleting blindly can destroy the only copy of work, and
never deleting rots the repo and pins branches forever.
worktree-janitor is the deterministic middle step: one
read-only pass that inventories every worktree and
local branch from git's own records, classifies each
worktree SAFE_TO_REMOVE / REVIEW / KEEP with printed
reasons, lists stale branches, and prints — never
executes — the exact cleanup commands for the SAFE
items.

**The safety core of this tool:** it cannot delete
anything. Every git invocation goes through an allowlist
gate that refuses mutating verbs/flags before git
starts; the plan exists only as output text; and all
calls run with `GIT_OPTIONAL_LOCKS=0` so git does not
even refresh the index. Running a scan or a plan on any
repo, however messy, changes nothing.

## When to use / not use

Use when:
- A repo has accumulated worktrees from agent sessions
  and you need to know which hold unique work before
  any cleanup.
- You are about to propose cleanup commands (as an
  agent) or run them (as a human): scan first, quote
  the verdicts and reasons, and let a human execute —
  or not.
- A weekly hygiene pass needs a yes/no gate: exit 0
  means nothing awaits cleanup; exit 1 hands you the
  itemised list.

Do NOT use when:
- You want the cleanup *done*. This tool never deletes.
  Executing the plan is a separate, human decision.
- The sprawl lives outside git: editor/agent-runner
  private worktree stashes and orphaned directories git
  has no registration for are invisible to it (stated in
  the report's honesty footer and the README's limits).
- You need disk sizes. v0.1.0 does not measure them.

## Commands

```bash
# Inventory + verdicts (text)
python3 src/worktree_janitor.py scan DIR

# Same facts as one JSON object — for scripts and agents
python3 src/worktree_janitor.py scan DIR --json

# The cleanup plan (printed commands, never executed)
python3 src/worktree_janitor.py plan DIR [--json]

# Tune the stale threshold (days; default 30)
python3 src/worktree_janitor.py scan DIR --stale-days 90

# No subcommand = scan on the current folder
python3 src/worktree_janitor.py
```

DIR may be any folder inside any worktree of the repo;
git resolves the whole repository from it.

## Verdict rules (exact)

Facts per worktree come from `git worktree list
--porcelain` (path, HEAD, branch, detached, bare,
locked + reason, prunable + reason), `git status
--porcelain=v1 --branch` in that worktree (change
counts, upstream, ahead/behind), and `git log` (tip
facts, range counts). The default branch is the main
worktree's checked-out branch; if it has none, the
fallback is a local branch named `main`, then `master`
— the basis used is always reported
(`default_branch_basis` in JSON).

- **KEEP** — the first (main) worktree; any worktree
  git reports as locked (its recorded reason is
  quoted); the worktree the scan was run from (when it
  is not the main one); a bare repository. A KEEP
  worktree's blockers, if any, are still listed as
  `note:` reasons.
- **SAFE_TO_REMOVE** — not KEEP, and either:
  (a) its directory is gone and git marks it prunable
  (clearing the registration cannot lose files; its
  branch remains in the branch inventory), or
  (b) status is readable and clean (0 changed, 0
  untracked), its branch is merged into the default
  branch, its unpushed count is 0, and it is not the
  current worktree.
- **REVIEW** — anything else, with every blocker as a
  reason: uncommitted changes (with staged/unstaged
  split), untracked files, branch not merged into the
  default (with the count of commits not in it),
  unpushed commits > 0 (with basis), detached HEAD,
  unreadable status, or a check that could not be
  verified (merge status unknown, unpushed count
  unavailable). Unknown is REVIEW, never SAFE.

Unpushed counts: with an upstream, commits in
`upstream..branch`; without one, commits in
`default..branch` — `unpushed_basis` names which, in
text and JSON. If the upstream ref is configured but
not present locally, the count is `null` and the check
appears in `checks_skipped`.

Branches: `merged` is against the default branch;
`stale` requires merged AND not the default AND not
checked out in any worktree AND tip age >=
--stale-days. `stale_after_prune` flags a branch that
meets the age/merge tests but is checked out *only* in
a missing (prunable) registration — the plan sequences
its `git branch -d` after the prune.

The stash is reported (count + entries) because it is
shared across all worktrees and survives every worktree
removal — listed so it is not forgotten, never acted
on.

## The plan (exact)

`build_plan` emits, in order: one `git worktree remove
<path>` per SAFE non-prunable worktree; a single
`git worktree prune` if any SAFE registration is
prunable; one `git branch -d <name>` per stale branch;
then one `git branch -d <name>` per stale-after-prune
branch (its reason states the prune dependency).
Deletions are always lowercase `-d` — git itself
refuses an unmerged branch — and `-D` appears nowhere.
REVIEW and KEEP items never appear in the plan. The
plan is data/text: this tool contains no code path
that executes it.

## Output JSON schema (`--json`)

Common fields: `tool` (`"worktree-janitor"`),
`version`, `command` (`"scan"` | `"plan"`), `repo`
(main worktree path), `default_branch`,
`default_branch_basis`, `stale_days`, `now_unix`,
`worktrees`, `branches`, `stale_branches`,
`stale_after_prune`, `stash` (`{count, entries}`),
`checks_skipped` (list of strings), `summary`, `clean`.
`plan` output adds `plan`: a list of `{kind, command,
target, reason}` with kind in `worktree-remove` |
`worktree-prune` | `branch-delete` |
`branch-delete-after-prune`.

Each worktree: `path`, `head`, `branch` (or `null`),
`detached`, `bare`, `locked`, `lock_reason`,
`prunable`, `prunable_reason`, `is_main`, `is_current`,
`status` (`ok` | `missing` | `bare` | `error`),
`changed_files`, `staged_files`, `unstaged_files`,
`untracked_files`, `upstream`, `ahead`, `behind`,
`last_commit` (`{sha, unix, date, age_days, subject}`
or `null`), `verdict`, `reasons` (list of strings).
Counts are `null` when the status could not be read.

Each branch: `name`, `upstream`, `tip`,
`checked_out_in` (path or `null`), `is_default`,
`merged` (bool or `null`), `commits_not_in_default`,
`unpushed_commits` (or `null`), `unpushed_basis`,
`last_commit`, `age_days`, `stale`,
`stale_after_prune`.

`summary`: `{worktrees, keep, safe_to_remove, review,
branches, stale_branches, stashes, findings}` where
`findings = safe_to_remove + review + stale_branches`.
`clean` is true exactly when `findings` is 0.

On error, stdout is a single object:
`{"error": "<message>"}` with exit code 2 — including
argparse usage errors in `--json` mode. Never plain
text, never a traceback.

## Exit codes

- `0` — nothing to clean: every worktree KEEP, no
  stale branches.
- `1` — at least one SAFE_TO_REMOVE or REVIEW worktree,
  or one stale branch. The repo is unchanged (it is
  always unchanged — the tool only reads).
- `2` — ERROR: not a git repository, folder missing,
  git not on PATH, invalid arguments. No facts may be
  quoted from a failed run.

## Limits & honesty rules the agent must respect

- **Never execute the plan yourself "to be helpful".**
  The commands are a proposal computed from git's
  facts at scan time. Present them; a human runs them.
  If you are an agent with shell access, running them
  anyway defeats the tool's entire design.
- **SAFE means git's facts cleared it — at scan time.**
  A worktree can gain uncommitted work a second later.
  Quote the verdict with its reasons and the scan's
  moment; suggest a re-scan right before any deletion.
- **REVIEW reasons are the deliverable.** Quote them
  exactly — "1 unpushed commit(s) (no upstream
  configured — counted against the default branch
  main)" is the fact; do not paraphrase it into
  "probably fine".
- **Skipped checks are named, never silent.** If
  `checks_skipped` is non-empty, say so when you
  summarise the run. A `null` unpushed count is
  "unknown", not "zero".
- **Git-visible state only.** Private worktree stashes
  kept by editors/agent runners outside the repo, and
  directories git has forgotten entirely, are not in
  this report. Do not claim "all worktrees" — say "all
  worktrees git knows about".
- Local only: the tool reads the one repository named,
  runs no network operations (not even a fetch), and
  sends nothing anywhere.
