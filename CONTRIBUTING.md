# Contributing

Contributions are welcome — bug reports, fixes and small focused features.

## Ground rules

- **Standard library only.** worktree-janitor's promise
  is "clone and run". Pull requests that add a
  third-party dependency will not be merged.
- **Local only.** No network calls, no telemetry, no
  uploads of user files. The git commands the tool runs
  must never perform network operations either — no
  fetch, ever. Freshness of upstream facts is the
  user's `git fetch` decision, not this tool's.
- **Read-only is the product — never weaken it.** Every
  git invocation must go through the allowlist gate,
  which refuses mutating verbs and flags before git
  starts, and must stay limited to pure queries
  (`worktree list`, `status`, `branch`, `log`,
  `stash list`). A PR that adds an `--execute` flag, a
  "cleanup for real" mode, or any code path from the
  plan to a shell will not be merged — the plan being
  inert text is the entire design. The suite records
  every argv and snapshots the fixture around runs;
  keep those tests green.
- **Verdicts are git's facts, never guesses.** A new
  rule must derive from git's own records (a query's
  output), print its reason, and fail toward REVIEW:
  anything that cannot be verified is REVIEW, never
  SAFE_TO_REMOVE. Heuristics that *infer* safety (name
  patterns, folder mtimes, "looks abandoned") will not
  be merged.
- **Skipped checks are named.** Any fact that can be
  unavailable (upstream absent, no default branch, an
  unreadable status) must surface in `checks_skipped`
  or as an explicit reason — never a silent pass, never
  a default of 0.
- **Plan hygiene.** Plan entries stay in the documented
  order (removes, prune, stale deletes, after-prune
  deletes), branch deletions are always lowercase `-d`,
  and `-D` must never be generated. REVIEW and KEEP
  items never enter the plan.
- **Both directions tested.** A behaviour change needs a
  test where the verdict fires and one where it does
  not, against real git repositories built in temp dirs
  (not mocked git output) wherever the behaviour depends
  on git's behaviour. Expected verdicts need
  hand-computed values in the test header, not output
  copied from the tool itself.

## Getting started

```bash
git clone https://github.com/jasveerai88/worktree-janitor
cd worktree-janitor
python3 -m unittest discover -s tests -v
python3 examples/make_sample.py /tmp/worktree-janitor-demo
python3 src/worktree_janitor.py scan /tmp/worktree-janitor-demo/repo
python3 src/worktree_janitor.py plan /tmp/worktree-janitor-demo/repo
```

## Pull requests

1. Fork, branch, and keep the change to one topic.
2. Add or update tests under `tests/`; the full suite must pass.
3. Note the change in `CHANGELOG.md` under an "Unreleased" heading.
4. Describe the behaviour change with a real before/after run if
   the CLI output changes.
