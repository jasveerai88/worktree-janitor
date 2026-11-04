#!/usr/bin/env python3
"""worktree-janitor — a read-only inventory of a git
repo's worktrees and branches, with a safe cleanup PLAN
it never executes.

Pure Python standard library. Local only: no network, no
uploads, no accounts. The tool shells out to git — but
only ever to git's read-only query commands:

    git worktree list --porcelain
    git status --porcelain=v1 --branch      (per worktree)
    git branch --format=... / --merged / --no-merged
    git log -1 / git log <range>            (counts + dates)
    git stash list

That list is enforced in code, not just promised: every
git invocation funnels through _run_git(), which refuses
any argv carrying a mutating verb or flag before git is
even started. All calls also run with GIT_OPTIONAL_LOCKS=0
so git itself does not take locks or refresh the index —
the scan changes nothing, not even a timestamp in .git.

Why this exists: parallel AI coding agents spawn git
worktrees constantly — one per session, per task, per
experiment. Headless runs leave theirs behind, locked.
Forgotten worktrees pile up, each pinning its branch,
each a folder of disk, and the only copy of some piece
of work may live in one of them. Deleting blindly loses
work; keeping everything rots the repo. The manual ritual
every guide prescribes — `git worktree list`, then walk
each folder checking status by hand — is exactly the
chore this tool makes one deterministic pass.

What it reports, per worktree: path, branch (or detached
HEAD), HEAD commit, dirty/clean with file counts, ahead /
behind vs its upstream (or commit counts vs the default
branch when no upstream is configured — the basis is
always named), last-commit age in days from git's own
committer timestamps, and the locked / prunable state
git itself reports. Each worktree gets one verdict:

    SAFE_TO_REMOVE  clean, branch merged into the default
                    branch, no unpushed commits, not
                    locked, not the main worktree, not
                    the worktree the scan was run from —
                    or a prunable registration whose
                    directory is already gone.
    REVIEW          something in it exists nowhere else
                    or cannot be verified: uncommitted
                    changes, untracked files, an unmerged
                    branch, unpushed commits, a detached
                    HEAD, or an unreadable status. The
                    exact reason is always printed.
    KEEP            the main worktree, a worktree git
                    reports as locked, the worktree the
                    scan was run from, or a bare repo.

Local branches are inventoried too (merged or not, tip
age, checked out where, upstream, unpushed count), and
merged branches whose last commit is at least
--stale-days old (default 30) and which are not checked
out anywhere are listed separately as STALE.

The `plan` subcommand prints the exact git commands that
WOULD remove the SAFE worktrees, prune dead
registrations, and delete the stale branches with
`git branch -d` (lowercase — git itself refuses if a
branch turns out unmerged; the plan never prints -D).
The commands are output text. This tool contains no code
path that executes them.

Honesty rules: every fact comes from git's own output.
When a check cannot be made — no default branch can be
determined, an upstream ref is not present locally — the
field says so and the check is named in checks_skipped,
never silently passed. A worktree whose merge or
unpushed status is unknown is REVIEW, never SAFE.

Exit codes: 0 = nothing to clean (every worktree KEEP,
no stale branches) · 1 = cleanup candidates or REVIEW
items exist · 2 = ERROR (not a git repo, git missing,
bad arguments) — a clean message, never a traceback;
with --json the error is {"error": ...}.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

__version__ = "1.0.0"
TOOL = "worktree-janitor"

SAFE = "SAFE_TO_REMOVE"
REVIEW = "REVIEW"
KEEP = "KEEP"
_VERDICT_ORDER = [REVIEW, SAFE, KEEP]

DEFAULT_STALE_DAYS = 30
_ZERO_SHA = "0" * 40


class ToolError(Exception):
    """A clean, user-facing failure (printed as ERROR,
    exit 2)."""


# --------------------------------------------------------------------------
# The read-only gate: every git call funnels through here
# --------------------------------------------------------------------------

# Verbs worktree-janitor may run at all. worktree/stash are
# further restricted to their "list" form below.
_ALLOWED_VERBS = {"worktree", "status", "branch", "log",
                  "stash"}

# Any argv element from this set — anywhere in any command
# — aborts the call before git starts. These are the
# mutating verbs and flags of the commands this tool uses.
_FORBIDDEN_TOKENS = {
    "add", "checkout", "switch", "clean", "commit",
    "restore", "reset", "update-ref", "rebase", "merge",
    "push", "pull", "fetch", "lock", "unlock", "move",
    "repair", "apply", "rm",
    "-d", "-D", "-m", "-M", "-c", "-C", "-f", "--force",
    "--delete", "--move", "--copy", "--set-upstream-to",
    "--unset-upstream", "--edit-description", "--prune",
    "--remove", "--dry-run",
}


def _assert_read_only(args):
    """Refuse any git argv that is not a pure query. This
    is the enforcement behind the read-only promise: the
    plan's command strings are built as text elsewhere and
    never pass through here — only real invocations do."""
    if not args or args[0] not in _ALLOWED_VERBS:
        raise AssertionError(
            f"git verb not on the read-only allowlist: "
            f"{args[:1]}")
    if args[0] in ("worktree", "stash") and (
            len(args) < 2 or args[1] != "list"):
        raise AssertionError(
            f"git {args[0]} is only ever run as "
            f"'{args[0]} list'")
    for tok in args:
        if tok in _FORBIDDEN_TOKENS:
            raise AssertionError(
                f"mutating token refused: {tok!r}")


def git_exe():
    return shutil.which("git")


def run_git(cwd, args):
    """Run one allowlisted git query in CWD. Returns
    (returncode, stdout, stderr). GIT_OPTIONAL_LOCKS=0
    keeps git from taking locks or refreshing the index,
    so a scan writes nothing — not even inside .git."""
    _assert_read_only(args)
    exe = git_exe()
    if exe is None:
        raise ToolError(
            "git was not found on PATH — worktree-janitor "
            "reads git's own records and cannot work "
            "without it")
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["LC_ALL"] = "C"
    proc = subprocess.run(
        [exe, "-C", str(cwd)] + list(args),
        capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout, proc.stderr


def git_query(cwd, args, what):
    """run_git for a query that must succeed; a failure is
    a clean ToolError naming WHAT was being read."""
    rc, out, err = run_git(cwd, args)
    if rc != 0:
        first = (err.strip().splitlines() or [""])[0]
        raise ToolError(f"could not read {what}: {first}")
    return out


# --------------------------------------------------------------------------
# Parsers for git's porcelain output
# --------------------------------------------------------------------------

def parse_worktree_list(text):
    """git worktree list --porcelain -> list of dicts:
    path, head, branch (short name or None), detached,
    bare, locked, lock_reason, prunable,
    prunable_reason."""
    worktrees = []
    cur = None
    for line in text.splitlines():
        if not line.strip():
            if cur is not None:
                worktrees.append(cur)
                cur = None
            continue
        if cur is None:
            cur = {"path": None, "head": None,
                   "branch": None, "detached": False,
                   "bare": False, "locked": False,
                   "lock_reason": None, "prunable": False,
                   "prunable_reason": None}
        if line.startswith("worktree "):
            cur["path"] = line[len("worktree "):]
        elif line.startswith("HEAD "):
            cur["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            prefix = "refs/heads/"
            cur["branch"] = (ref[len(prefix):]
                             if ref.startswith(prefix)
                             else ref)
        elif line == "detached":
            cur["detached"] = True
        elif line == "bare":
            cur["bare"] = True
        elif line == "locked" or line.startswith("locked "):
            cur["locked"] = True
            cur["lock_reason"] = (line[len("locked "):]
                                  if line.startswith("locked ")
                                  else None)
        elif line == "prunable" or line.startswith("prunable "):
            cur["prunable"] = True
            cur["prunable_reason"] = (
                line[len("prunable "):]
                if line.startswith("prunable ") else None)
    if cur is not None:
        worktrees.append(cur)
    return worktrees


_HEADER_RE = re.compile(
    r"^## (?:No commits yet on (?P<fresh>.+)|"
    r"HEAD \(no branch\)|"
    r"(?P<branch>[^.\s]+(?:\.[^.\s]+)*)"
    r"(?:\.\.\.(?P<upstream>\S+))?"
    r"(?: \[(?P<track>[^\]]+)\])?)$")


def parse_status(text):
    """git status --porcelain=v1 --branch -> dict:
    branch, upstream, ahead, behind, detached, no_commits,
    changed (tracked files with staged or unstaged
    changes), staged, unstaged, untracked."""
    st = {"branch": None, "upstream": None, "ahead": None,
          "behind": None, "detached": False,
          "no_commits": False, "changed": 0, "staged": 0,
          "unstaged": 0, "untracked": 0}
    for line in text.splitlines():
        if line.startswith("## "):
            m = _HEADER_RE.match(line)
            if m:
                if m.group("fresh") is not None:
                    st["no_commits"] = True
                    st["branch"] = m.group("fresh")
                elif m.group("branch") is None:
                    st["detached"] = True
                else:
                    st["branch"] = m.group("branch")
                    st["upstream"] = m.group("upstream")
                track = m.group("track") or ""
                ma = re.search(r"ahead (\d+)", track)
                mb = re.search(r"behind (\d+)", track)
                if ma:
                    st["ahead"] = int(ma.group(1))
                if mb:
                    st["behind"] = int(mb.group(1))
                if st["upstream"] is not None:
                    if st["ahead"] is None:
                        st["ahead"] = 0
                    if st["behind"] is None:
                        st["behind"] = 0
            continue
        if len(line) < 2:
            continue
        x, y = line[0], line[1]
        if x == "?" and y == "?":
            st["untracked"] += 1
            continue
        if x == "!" and y == "!":
            continue
        st["changed"] += 1
        if x not in " ?":
            st["staged"] += 1
        if y not in " ?":
            st["unstaged"] += 1
    return st


# --------------------------------------------------------------------------
# Small git fact helpers
# --------------------------------------------------------------------------

def _commit_info(repo, ref):
    """{sha, unix, date, age_days, subject} for REF's tip
    commit, or None when REF has no commit (unborn branch,
    empty repo) or git cannot read it. Dates are the
    committer timestamps git records."""
    if not ref or ref == _ZERO_SHA:
        return None
    rc, out, _ = run_git(
        repo, ["log", "-1", "--format=%H%x1f%ct%x1f%s", ref])
    if rc != 0 or not out.strip():
        return None
    sha, ct, subject = out.rstrip("\n").split("\x1f", 2)
    return {"sha": sha, "unix": int(ct), "subject": subject}


def _age(info, now):
    if info is None:
        return None
    age = max(0, int((now - info["unix"]) // 86400))
    info["age_days"] = age
    info["date"] = datetime.fromtimestamp(
        info["unix"], tz=timezone.utc).strftime("%Y-%m-%d")
    return age


def _range_count(repo, range_spec):
    """Number of commits in RANGE_SPEC, or None when git
    cannot evaluate it (e.g. the upstream ref is not
    present locally)."""
    rc, out, _ = run_git(
        repo, ["log", "--format=%H", range_spec])
    if rc != 0:
        return None
    return sum(1 for ln in out.splitlines() if ln.strip())


# --------------------------------------------------------------------------
# The scan
# --------------------------------------------------------------------------

def scan(d, stale_days):
    """Full inventory + verdicts for the repo containing
    DIR D (any folder inside any of its worktrees works —
    git resolves the whole repo from it)."""
    d = Path(d)
    now = int(time.time())
    skipped = []

    raw = git_query(d, ["worktree", "list", "--porcelain"],
                    "the worktree list (is this a git "
                    "repository?)")
    raw_wts = parse_worktree_list(raw)
    if not raw_wts:
        raise ToolError("git reported no worktrees — is "
                        "this a git repository?")
    repo_root = Path(raw_wts[0]["path"])

    # The worktree the scan was run from: the longest
    # worktree path that is DIR itself or a parent of it.
    try:
        here = str(Path(d).resolve())
    except OSError:
        here = str(d)
    current_path = None
    for w in raw_wts:
        p = str(Path(w["path"]).resolve()) \
            if w["path"] else None
        if p and (here == p or here.startswith(p + os.sep)):
            if current_path is None or len(p) > len(
                    current_path):
                current_path = p

    # Local branch inventory: name, upstream, tip — one
    # branch listing call with for-each-ref atoms.
    branches = []
    fmt = "%(refname:short)%09%(upstream:short)%09%(objectname)"
    bout = git_query(repo_root, ["branch", f"--format={fmt}"],
                     "the local branch list")
    for line in bout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        branches.append({
            "name": parts[0],
            "upstream": parts[1] or None
            if len(parts) > 1 else None,
            "tip": parts[2] if len(parts) > 2 else None})
    by_name = {b["name"]: b for b in branches}

    # Default branch: the branch of the main worktree —
    # git's own record. Fallbacks are named, never silent.
    default = None
    basis = None
    if raw_wts[0]["branch"]:
        default = raw_wts[0]["branch"]
        basis = "the main worktree's checked-out branch"
    elif "main" in by_name:
        default = "main"
        basis = "fallback: a local branch named 'main' exists"
    elif "master" in by_name:
        default = "master"
        basis = "fallback: a local branch named 'master' exists"
    if default is None:
        skipped.append(
            "merge status and unpushed counts: no default "
            "branch could be determined (the main worktree "
            "has no branch checked out and no local branch "
            "is named main or master)")

    merged = None
    if default is not None:
        rc, mout, _ = run_git(
            repo_root, ["branch", "--merged", default,
                        "--format=%(refname:short)"])
        if rc != 0:
            # An unborn default branch (empty repo) has
            # nothing merged into it by definition.
            merged = set()
            skipped.append(
                f"merged-branch list: '{default}' has no "
                f"commits yet, so no branch can be merged "
                f"into it")
        else:
            merged = {ln.strip() for ln in mout.splitlines()
                      if ln.strip()}

    checked_out = {}
    for w in raw_wts:
        if w["branch"]:
            checked_out[w["branch"]] = w["path"]

    # ---- branch records ------------------------------------
    for b in branches:
        info = _commit_info(repo_root, b["name"])
        b["last_commit"] = info
        b["age_days"] = _age(info, now)
        b["checked_out_in"] = checked_out.get(b["name"])
        b["is_default"] = (b["name"] == default)
        b["merged"] = (b["name"] in merged
                       if merged is not None else None)
        not_in_default = None
        if default is not None:
            if b["name"] == default:
                not_in_default = 0
            else:
                not_in_default = _range_count(
                    repo_root, f"{default}..{b['name']}")
        b["commits_not_in_default"] = not_in_default
        if b["upstream"]:
            n = _range_count(
                repo_root, f"{b['upstream']}..{b['name']}")
            if n is None:
                b["unpushed_commits"] = None
                b["unpushed_basis"] = (
                    f"upstream {b['upstream']} is configured "
                    f"but not present locally — count "
                    f"unavailable")
                skipped.append(
                    f"unpushed count for branch "
                    f"{b['name']}: upstream "
                    f"{b['upstream']} not present locally")
            else:
                b["unpushed_commits"] = n
                b["unpushed_basis"] = (
                    f"vs upstream {b['upstream']}")
        elif b["is_default"]:
            b["unpushed_commits"] = 0
            b["unpushed_basis"] = "the default branch itself"
        elif default is not None:
            b["unpushed_commits"] = not_in_default
            b["unpushed_basis"] = (
                f"no upstream configured — counted against "
                f"the default branch {default}")
        else:
            b["unpushed_commits"] = None
            b["unpushed_basis"] = (
                "no upstream configured and no default "
                "branch determined — count unavailable")
        b["stale"] = bool(
            b["merged"] is True
            and not b["is_default"]
            and b["checked_out_in"] is None
            and b["age_days"] is not None
            and b["age_days"] >= stale_days)

    # ---- worktree records + verdicts ------------------------
    worktrees = []
    for i, w in enumerate(raw_wts):
        rec = dict(w)
        rec["is_main"] = (i == 0)
        rec["is_current"] = (
            current_path is not None
            and str(Path(w["path"]).resolve()) == current_path)
        rec["status"] = None
        rec["changed_files"] = None
        rec["staged_files"] = None
        rec["unstaged_files"] = None
        rec["untracked_files"] = None
        rec["upstream"] = None
        rec["ahead"] = None
        rec["behind"] = None
        info = _commit_info(repo_root, w["head"])
        rec["last_commit"] = info
        rec["age_days"] = _age(info, now)

        blockers = []
        notes = []
        if w["bare"]:
            rec["status"] = "bare"
        elif w["prunable"] or not Path(w["path"]).is_dir():
            rec["status"] = "missing"
        else:
            rc, out, err = run_git(
                w["path"],
                ["status", "--porcelain=v1", "--branch"])
            if rc != 0:
                rec["status"] = "error"
                first = (err.strip().splitlines()
                         or ["unknown git error"])[0]
                blockers.append(
                    f"working-tree status could not be read "
                    f"({first}) — its contents are unverified")
            else:
                rec["status"] = "ok"
                st = parse_status(out)
                rec["changed_files"] = st["changed"]
                rec["staged_files"] = st["staged"]
                rec["unstaged_files"] = st["unstaged"]
                rec["untracked_files"] = st["untracked"]
                rec["upstream"] = st["upstream"]
                rec["ahead"] = st["ahead"]
                rec["behind"] = st["behind"]

        brec = by_name.get(w["branch"]) if w["branch"] \
            else None
        if rec["status"] == "ok":
            if rec["changed_files"]:
                blockers.append(
                    f"{rec['changed_files']} uncommitted "
                    f"change(s) (staged "
                    f"{rec['staged_files']}, unstaged "
                    f"{rec['unstaged_files']}) — they exist "
                    f"only in this worktree")
            if rec["untracked_files"]:
                blockers.append(
                    f"{rec['untracked_files']} untracked "
                    f"file(s) — they exist only in this "
                    f"worktree")
            if w["detached"]:
                blockers.append(
                    "detached HEAD — no branch holds this "
                    "worktree's position, so its merge and "
                    "unpushed state cannot be verified")
            elif brec is not None:
                if brec["merged"] is False:
                    blockers.append(
                        f"branch {w['branch']} is not merged "
                        f"into {default} "
                        f"({brec['commits_not_in_default']} "
                        f"commit(s) not in {default})")
                elif brec["merged"] is None:
                    blockers.append(
                        f"merge status of branch "
                        f"{w['branch']} is unknown — no "
                        f"default branch could be determined")
                if brec["unpushed_commits"]:
                    blockers.append(
                        f"{brec['unpushed_commits']} "
                        f"unpushed commit(s) "
                        f"({brec['unpushed_basis']})")
                elif brec["unpushed_commits"] is None:
                    blockers.append(
                        f"unpushed state of branch "
                        f"{w['branch']} could not be "
                        f"verified ({brec['unpushed_basis']})")

        reasons = []
        if w["bare"]:
            verdict = KEEP
            reasons.append("bare repository — no working "
                           "tree to clean")
        elif rec["is_main"]:
            verdict = KEEP
            reasons.append("the main worktree")
            reasons.extend(f"note: {b}" for b in blockers)
        elif w["locked"]:
            verdict = KEEP
            r = w["lock_reason"]
            reasons.append(
                f"locked by git"
                + (f" — reason recorded by git: {r}" if r
                   else " (no reason recorded)"))
            reasons.extend(f"note: {b}" for b in blockers)
        elif rec["status"] == "missing":
            verdict = SAFE
            pr = w["prunable_reason"]
            reasons.append(
                "the directory is gone and git marks this "
                "worktree prunable"
                + (f" ({pr})" if pr else "")
                + " — clearing the registration cannot "
                  "lose files; its branch, if any, stays "
                  "in the branch list below")
        elif blockers:
            verdict = REVIEW
            reasons.extend(blockers)
        elif rec["is_current"]:
            verdict = KEEP
            reasons.append("the worktree this scan was run "
                           "from — a janitor does not sweep "
                           "the floor it stands on")
        else:
            verdict = SAFE
            reasons.append(
                f"clean, branch {w['branch']} merged into "
                f"{default}, no unpushed commits, not "
                f"locked")
        if rec["is_current"] and not rec["is_main"]:
            reasons.append("this is the worktree the scan "
                           "was run from")
        rec["verdict"] = verdict
        rec["reasons"] = reasons
        worktrees.append(rec)

    # A branch checked out ONLY in a registration whose
    # directory is already gone becomes stale the moment
    # the prune clears that registration — flag it now so
    # the plan can sequence it after the prune.
    missing_paths = {w["path"] for w in worktrees
                     if w["status"] == "missing"}
    for b in branches:
        b["stale_after_prune"] = bool(
            b["merged"] is True
            and not b["is_default"]
            and not b["stale"]
            and b["checked_out_in"] in missing_paths
            and b["age_days"] is not None
            and b["age_days"] >= stale_days)

    # ---- stash (shared across all worktrees) ---------------
    rc, sout, _ = run_git(repo_root, ["stash", "list"])
    if rc != 0:
        # git refuses to list the stash outside a work
        # tree (a bare repository). Named, never silent.
        skipped.append(
            "stash list: git would not list the stash "
            "here (bare repository — the command needs a "
            "work tree)")
        sout = ""
    stash_entries = [ln for ln in sout.splitlines()
                     if ln.strip()]

    stale = [b["name"] for b in branches if b["stale"]]
    summary = {
        "worktrees": len(worktrees),
        "keep": sum(1 for w in worktrees
                    if w["verdict"] == KEEP),
        "safe_to_remove": sum(1 for w in worktrees
                              if w["verdict"] == SAFE),
        "review": sum(1 for w in worktrees
                      if w["verdict"] == REVIEW),
        "branches": len(branches),
        "stale_branches": len(stale),
        "stashes": len(stash_entries),
    }
    summary["findings"] = (summary["safe_to_remove"]
                           + summary["review"]
                           + summary["stale_branches"])
    return {"repo": str(repo_root),
            "default_branch": default,
            "default_branch_basis": basis,
            "stale_days": stale_days,
            "now_unix": now,
            "worktrees": worktrees,
            "branches": branches,
            "stale_branches": stale,
            "stale_after_prune": [b["name"] for b in branches
                                  if b["stale_after_prune"]],
            "stash": {"count": len(stash_entries),
                      "entries": stash_entries},
            "checks_skipped": skipped,
            "summary": summary,
            "clean": summary["findings"] == 0}


# --------------------------------------------------------------------------
# The plan — printed commands, never executed
# --------------------------------------------------------------------------

def build_plan(result):
    """The exact commands that WOULD clean up the SAFE
    items. Returned as data; printing is all that ever
    happens to them."""
    plan = []
    need_prune = False
    for w in result["worktrees"]:
        if w["verdict"] != SAFE:
            continue
        if w["status"] == "missing":
            need_prune = True
            plan.append({
                "kind": "worktree-prune",
                "command": "git worktree prune",
                "target": w["path"],
                "reason": "registration whose directory is "
                          "already gone (one prune clears "
                          "every such registration)"})
        else:
            plan.append({
                "kind": "worktree-remove",
                "command": "git worktree remove "
                           + shlex.quote(w["path"]),
                "target": w["path"],
                "reason": "; ".join(w["reasons"])})
    if need_prune:
        plan = [p for p in plan
                if p["kind"] != "worktree-prune"] + [{
                    "kind": "worktree-prune",
                    "command": "git worktree prune",
                    "target": None,
                    "reason": "clears every prunable "
                              "worktree registration whose "
                              "directory is already gone"}]
    for name in result["stale_branches"]:
        plan.append({
            "kind": "branch-delete",
            "command": "git branch -d " + shlex.quote(name),
            "target": name,
            "reason": f"merged into "
                      f"{result['default_branch']}, last "
                      f"commit at least "
                      f"{result['stale_days']} day(s) old, "
                      f"not checked out anywhere — "
                      f"lowercase -d, so git itself refuses "
                      f"if the branch is somehow unmerged"})
    for name in result.get("stale_after_prune", []):
        plan.append({
            "kind": "branch-delete-after-prune",
            "command": "git branch -d " + shlex.quote(name),
            "target": name,
            "reason": f"merged into "
                      f"{result['default_branch']} and at "
                      f"least {result['stale_days']} day(s) "
                      f"old, but still checked out in a "
                      f"dead worktree registration — run "
                      f"this only AFTER the prune above has "
                      f"cleared that registration"})
    return plan


# --------------------------------------------------------------------------
# Text rendering
# --------------------------------------------------------------------------

def _commit_phrase(rec):
    info = rec.get("last_commit")
    if info is None:
        return "no commits yet"
    return (f"last commit {info['age_days']} day(s) ago, "
            f"{info['date']} (UTC) — "
            f"\"{info['subject']}\"")


def _worktree_lines(w):
    head = (w["head"] or "")[:7] or "—"
    if w["bare"]:
        top = f"  [{w['verdict']}] {w['path']} — bare"
    elif w["detached"]:
        top = f"  [{w['verdict']}] {w['path']} — detached @ {head}"
    else:
        top = (f"  [{w['verdict']}] {w['path']} — branch "
               f"{w['branch']} @ {head}")
    lines = [top]
    if w["status"] == "ok":
        if w["changed_files"] or w["untracked_files"]:
            state = (f"{w['changed_files']} uncommitted "
                     f"change(s), {w['untracked_files']} "
                     f"untracked file(s)")
        else:
            state = "clean"
        extra = ""
        if w["upstream"]:
            extra = (f" · upstream {w['upstream']} "
                     f"(ahead {w['ahead']}, behind "
                     f"{w['behind']})")
        lines.append(f"      {state} · "
                     f"{_commit_phrase(w)}{extra}")
    elif w["status"] == "missing":
        lines.append("      directory gone (prunable)")
    lines.append(f"      why: {'; '.join(w['reasons'])}")
    return lines


def render_header(result, command):
    repo = Path(result["repo"])
    s = result["summary"]
    dflt = result["default_branch"]
    lines = [f"{TOOL} v{__version__} — {command} · repo "
             f"{repo.name}"]
    lines.append(
        f"{result['repo']} · default branch "
        f"{dflt if dflt else 'unknown'}"
        f" ({result['default_branch_basis'] or '—'})"
        f" · stale threshold "
        f"{result['stale_days']} day(s)")
    lines.append(
        f"{s['worktrees']} worktree(s) — {s['keep']} KEEP"
        f" · {s['safe_to_remove']} SAFE_TO_REMOVE"
        f" · {s['review']} REVIEW · {s['branches']} local"
        f" branch(es) · {s['stale_branches']} stale"
        f" · {s['stashes']} stash entr"
        f"{'y' if s['stashes'] == 1 else 'ies'}")
    return lines


def render_skipped(result):
    if not result["checks_skipped"]:
        return []
    lines = ["", "CHECKS SKIPPED — named, never silent:"]
    for c in result["checks_skipped"]:
        lines.append(f"  - {c}")
    return lines


def render_scan(result):
    lines = render_header(result, "scan")
    lines.append("")
    lines.append("WORKTREES:")
    for verdict in _VERDICT_ORDER:
        for w in result["worktrees"]:
            if w["verdict"] == verdict:
                lines.extend(_worktree_lines(w))
    lines.append("")
    lines.append("BRANCHES:")
    if not result["branches"]:
        lines.append("  (no local branches — no commits yet)")
    for b in result["branches"]:
        bits = [b["name"]]
        if b["is_default"]:
            bits.append("default")
        if b["checked_out_in"]:
            bits.append(f"checked out at "
                        f"{b['checked_out_in']}")
        if b["merged"] is True:
            bits.append(f"merged into "
                        f"{result['default_branch']}")
        elif b["merged"] is False:
            bits.append(f"NOT merged into "
                        f"{result['default_branch']} "
                        f"({b['commits_not_in_default']} "
                        f"commit(s) not in it)")
        else:
            bits.append("merge status unknown")
        if b["last_commit"] is not None:
            bits.append(_commit_phrase(b))
        if b["upstream"]:
            bits.append(f"upstream {b['upstream']}, "
                        f"{b['unpushed_commits']} unpushed")
        if b["stale"]:
            bits.append("STALE")
        elif b.get("stale_after_prune"):
            bits.append("STALE once its dead worktree "
                        "registration is pruned")
        lines.append("  " + " · ".join(bits))
    if result["stash"]["count"]:
        lines.append("")
        lines.append(
            f"STASH: {result['stash']['count']} entr"
            f"{'y' if result['stash']['count'] == 1 else 'ies'}"
            f" — the stash is shared across all worktrees;"
            f" removing a worktree never touches it, and "
            f"neither does this tool:")
        for e in result["stash"]["entries"]:
            lines.append(f"  {e}")
    lines.extend(render_skipped(result))
    lines.append("")
    if result["clean"]:
        lines.append(
            "CLEAN — every worktree is a keeper and no "
            "branch is stale. Nothing awaits cleanup.")
    else:
        lines.append(
            "VERDICT: cleanup candidates exist — run the "
            "`plan` subcommand for the exact commands, "
            "review the REVIEW items by hand first.")
    lines.append(
        "worktree-janitor only reads: every fact above "
        "came from git's own read-only queries, nothing "
        "was executed, removed or changed — not even an "
        "index refresh (GIT_OPTIONAL_LOCKS=0). Verdicts "
        "are git's facts (clean / merged / unpushed / "
        "locked), never a guess; a check that could not "
        "be made is named under CHECKS SKIPPED and its "
        "worktree is REVIEW, never SAFE.")
    return "\n".join(lines)


def render_plan(result):
    plan = build_plan(result)
    lines = render_header(result, "plan")
    lines.append("")
    if not plan:
        lines.append(
            "NOTHING TO DO — no worktree is SAFE_TO_REMOVE "
            "and no branch is stale. (REVIEW items, if "
            "any, are deliberately not in this plan: "
            "something in them exists nowhere else.)")
    else:
        lines.append(
            "CLEANUP PLAN — the exact commands, in order. "
            "worktree-janitor does NOT run them; copy them "
            "yourself after reading the REVIEW items:")
        lines.append("")
        for i, p in enumerate(plan, 1):
            lines.append(f"  {p['command']}")
            lines.append(f"      # {p['reason']}")
        lines.append("")
        lines.append(
            "After running them, re-run "
            "`python3 src/worktree_janitor.py scan` to "
            "confirm the repo is CLEAN. Branch deletions "
            "use lowercase -d: git itself refuses if a "
            "branch is somehow unmerged. There is no -D "
            "anywhere in this plan, deliberately.")
    if result["summary"]["review"]:
        lines.append("")
        lines.append(
            f"NOT IN THE PLAN: "
            f"{result['summary']['review']} worktree(s) "
            f"marked REVIEW — each holds something that "
            f"exists nowhere else. See the scan report.")
    lines.extend(render_skipped(result))
    lines.append("")
    lines.append(
        "worktree-janitor only reads: this plan was "
        "computed from git's own read-only queries and "
        "printed as text. Nothing was executed, removed "
        "or changed.")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

class _JsonAwareParser(argparse.ArgumentParser):
    """ArgumentParser that reports usage errors as JSON in
    --json mode ({"error": ...} on stdout, same exit code
    2) — an agent driving this tool with --json never gets
    plain text or a traceback. The flag lives on the class
    so subparsers share it."""

    json_mode = False

    def error(self, message):
        if self.json_mode:
            print(json.dumps({"error": message}))
            self.exit(2)
        super().error(message)


def _add_common(ap):
    ap.add_argument("dir", nargs="?", default=".",
                    help="any folder inside the repository "
                         "(default: current folder)")
    ap.add_argument("--json", action="store_true",
                    help="print one JSON object instead of "
                         "text")
    ap.add_argument("--stale-days", type=int,
                    default=DEFAULT_STALE_DAYS, metavar="N",
                    help="a merged branch whose last commit "
                         "is at least N days old counts "
                         "as stale (default: "
                         f"{DEFAULT_STALE_DAYS})")


def _require_dir(raw):
    d = Path(raw)
    if not d.exists():
        raise ToolError(f"no such folder: {raw}")
    if not d.is_dir():
        raise ToolError(f"not a folder: {raw}")
    return d


def _run(command, a):
    if a.stale_days < 0:
        raise ToolError("--stale-days must be 0 or more")
    if git_exe() is None:
        raise ToolError(
            "git was not found on PATH — worktree-janitor "
            "reads git's own records and cannot work "
            "without it")
    d = _require_dir(a.dir)
    result = scan(d, a.stale_days)
    exit_code = 0 if result["clean"] else 1
    if command == "scan":
        if a.json:
            print(json.dumps(
                {"tool": TOOL, "version": __version__,
                 "command": "scan", **result}, indent=2))
        else:
            print(render_scan(result))
        return exit_code
    plan = build_plan(result)
    if a.json:
        print(json.dumps(
            {"tool": TOOL, "version": __version__,
             "command": "plan", **result, "plan": plan},
            indent=2))
    else:
        print(render_plan(result))
    return exit_code


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    _JsonAwareParser.json_mode = "--json" in argv
    if argv and argv[0] not in ("scan", "plan", "-h",
                                "--help"):
        argv = ["scan"] + argv
    if not argv:
        argv = ["scan"]
    ap = _JsonAwareParser(
        prog=TOOL,
        description="Read-only inventory of a git repo's "
                    "worktrees and branches: dirty, "
                    "unpushed, merged, locked, prunable, "
                    "stale — with verdicts, and a cleanup "
                    "plan it prints but never executes. "
                    "(stdlib only, local only).")
    sub = ap.add_subparsers(dest="command",
                            parser_class=_JsonAwareParser)
    p_scan = sub.add_parser(
        "scan", help="inventory + verdicts (default)")
    _add_common(p_scan)
    p_plan = sub.add_parser(
        "plan", help="print the exact cleanup commands "
                     "for the SAFE items — never runs them")
    _add_common(p_plan)
    a = ap.parse_args(argv)
    command = a.command or "scan"
    if not hasattr(a, "dir"):
        # Bare `scan`/`plan` with no further args reaches
        # here only if argparse defaults were bypassed;
        # normalise defensively.
        a.dir, a.json, a.stale_days = ".", False, \
            DEFAULT_STALE_DAYS

    def fail(msg):
        if getattr(a, "json", False):
            print(json.dumps({"error": msg}))
        else:
            print(f"ERROR: {msg}")
        return 2

    try:
        return _run(command, a)
    except ToolError as exc:
        return fail(str(exc))


if __name__ == "__main__":
    sys.exit(main())
