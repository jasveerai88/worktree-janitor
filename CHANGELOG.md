# Changelog

## v0.1.0 — 2026-11-04

First release.

- **A read-only inventory for agent worktree sprawl.**
  Born from the mess parallel AI coding sessions leave
  behind: forgotten worktrees that "multiply like coat
  hangers", headless runs whose worktrees stay on disk
  locked, branches pinned by registrations nobody
  remembers creating. Every guide prescribes the same
  manual ritual — `git worktree list`, then walk each
  folder checking status by hand. worktree-janitor makes
  it one deterministic pass over git's own records.
- **Per-worktree facts** — path, branch or detached
  HEAD, dirty/clean with staged / unstaged / untracked
  counts, ahead/behind vs upstream, last-commit age in
  days from git's committer timestamps (dates in UTC),
  and the locked / prunable state (with git's recorded
  reasons) — from exactly five read-only git queries:
  `worktree list --porcelain`, `status --porcelain=v1
  --branch`, `branch --format / --merged`, `log`,
  `stash list`.
- **Verdicts with printed reasons** — SAFE_TO_REMOVE
  (clean, merged into the default branch, no unpushed
  commits, not locked, not the main worktree, not the
  worktree the scan was run from — or a prunable
  registration whose directory is already gone),
  REVIEW (uncommitted or untracked work, unmerged
  branch, unpushed commits, detached HEAD, or any check
  that could not be verified — unknown is REVIEW, never
  SAFE), KEEP (main worktree, locked, current worktree,
  bare repo). The default branch is the main worktree's
  branch, with named fallbacks (`main`, then `master`).
- **Branch inventory + staleness** — merged or not, tip
  age, checked out where, upstream, unpushed count.
  STALE = merged + checked out nowhere + last commit at
  least `--stale-days` old (default 30). A branch
  checked out only in a dead registration is flagged
  `stale_after_prune` so the plan can sequence its
  deletion after the prune that frees it.
- **Unpushed counts with a named basis** — against the
  upstream when one is configured, against the default
  branch when it is not; the basis is printed in text
  and carried in JSON (`unpushed_basis`). An upstream
  configured but absent locally yields a `null` count
  and a named entry in `checks_skipped` — a skipped
  check is reported, never silently passed.
- **A plan that is never executed** — `plan` prints, in
  order, `git worktree remove` for each SAFE worktree,
  one `git worktree prune` if any registration is
  prunable, and `git branch -d` (lowercase only — git
  itself refuses an unmerged branch; `-D` appears
  nowhere) for stale and stale-after-prune branches.
  The commands are assembled as text; the tool contains
  no code path that runs them, and REVIEW items never
  appear in the plan.
- **Read-only, enforced in code** — every git call
  funnels through a single gate that refuses any argv
  carrying a mutating verb or flag before git starts,
  and all calls run with `GIT_OPTIONAL_LOCKS=0` so git
  does not take locks or refresh the index. The test
  suite records every argv of a scan + plan run and
  asserts allowlist compliance, and snapshots the full
  fixture state (git listings + every working-tree
  file) around both commands requiring exact equality.
- **Edge repos behave** — an empty repo (no commits),
  a bare repo (where git refuses `stash list` outside a
  work tree — surfaced as a named skipped check), a
  folder that is not a repo, and a missing git binary
  all end in clean exit-2 errors, never tracebacks; in
  `--json` mode errors are `{"error": ...}` objects,
  argparse usage errors included.
- **`--json` on both subcommands** — one JSON object
  with the same facts and exit codes as the text view
  (per-worktree and per-branch records, stash,
  checks_skipped, summary, `clean`; plan output adds
  the sequenced command list). Exit codes: 0 nothing
  to clean · 1 candidates or REVIEW items · 2 error.
- **`SKILL.md`** at the repo root for AI coding agents:
  verdict rules at their exact edges, the JSON schema,
  exit codes, and the honesty rules (never execute the
  plan; SAFE is a fact about scan time; skipped checks
  are named; scope is git-visible state only).
- Bundled sample generator (`examples/make_sample.py`)
  plus captured real runs (`examples/sample-report.txt`,
  `sample-plan.txt`, `sample-report.json`): a repo with
  6 worktrees (2 KEEP · 2 SAFE_TO_REMOVE · 2 REVIEW),
  8 branches, exactly 1 stale branch and 1 stash entry
  — the same shape the test suite builds and asserts,
  with all expectations worked out by hand in the
  test-suite header.
- 36 stdlib-unittest tests against REAL git
  repositories built with the git CLI in temp dirs
  (merged/unmerged/dirty/locked/detached/prunable
  worktrees, a stash, a real local upstream, empty and
  bare repos), plus parser unit tests on synthetic
  porcelain and the read-only proofs described above.
- Pure Python standard library; local only; no network
  code; MIT licence.
