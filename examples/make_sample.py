#!/usr/bin/env python3
"""Build the bundled sample repo for worktree-janitor.

    python3 examples/make_sample.py /tmp/worktree-janitor-demo

Creates a small, fully local git repo with the exact
sprawl parallel agents leave behind:

    repo/        main worktree (branch main) + 1 stash
    wt-safe/     merged + clean — SAFE_TO_REMOVE
    wt-dirty/    unmerged branch, 1 uncommitted change,
                 1 untracked file — REVIEW
    wt-locked/   clean + merged but `git worktree lock`ed
                 (reason: "agent session running") — KEEP
    wt-detached/ detached HEAD — REVIEW
    wt-gone/     created, then its folder is deleted —
                 git marks the registration prunable

plus branches: feat-merged-old (merged, tip dated
2020-01-02 — the one STALE branch), feat-merged-new
(merged, recent), feat-unmerged (1 commit main lacks).

Everything is local and inert: the "old" commit is just
dated 2020 via git's own date environment variables, and
nothing here talks to a network. Re-running into an
existing folder replaces it.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

OLD_DATE = "2020-01-02T03:04:05 +0000"


def git(cwd, *args, date=None):
    env = dict(os.environ)
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    subprocess.run(["git", "-C", str(cwd), *args],
                   check=True, env=env,
                   capture_output=True, text=True)


def commit_all(repo, message, date=None):
    f = Path(repo) / "file.txt"
    with open(f, "a") as fh:
        fh.write(message + "\n")
    git(repo, "add", "file.txt")
    git(repo, "commit", "-q", "-m", message, date=date)


def main(argv):
    if len(argv) != 2:
        print("usage: make_sample.py TARGET_DIR")
        return 2
    base = Path(argv[1])
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    repo = base / "repo"
    proc = subprocess.run(
        ["git", "init", "-q", "-b", "main", str(repo)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        subprocess.run(["git", "init", "-q", str(repo)],
                       check=True, capture_output=True)
        git(repo, "symbolic-ref", "HEAD",
            "refs/heads/main")
    git(repo, "config", "user.email", "sample@example.com")
    git(repo, "config", "user.name", "Sample User")
    git(repo, "config", "commit.gpgsign", "false")

    commit_all(repo, "base")
    git(repo, "checkout", "-q", "-b", "feat-merged-old")
    commit_all(repo, "old work", date=OLD_DATE)
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge old",
        "feat-merged-old")
    git(repo, "checkout", "-q", "-b", "feat-merged-new")
    commit_all(repo, "new work")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge new",
        "feat-merged-new")
    git(repo, "checkout", "-q", "-b", "feat-unmerged")
    commit_all(repo, "unmerged work")
    git(repo, "checkout", "-q", "main")
    git(repo, "checkout", "-q", "-b", "feat-done")
    commit_all(repo, "done work")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge done",
        "feat-done")

    git(repo, "worktree", "add", "-q",
        str(base / "wt-safe"), "feat-done")
    git(repo, "worktree", "add", "-q", "-b", "feat-wip",
        str(base / "wt-dirty"))
    commit_all(base / "wt-dirty", "wip work")
    with open(base / "wt-dirty" / "file.txt", "a") as fh:
        fh.write("uncommitted edit\n")
    (base / "wt-dirty" / "untracked.txt").write_text(
        "loose\n")
    git(repo, "worktree", "add", "-q", "-b", "feat-locked",
        str(base / "wt-locked"))
    git(repo, "worktree", "lock", "--reason",
        "agent session running", str(base / "wt-locked"))
    git(repo, "worktree", "add", "-q", "--detach",
        str(base / "wt-detached"))
    git(repo, "worktree", "add", "-q", "-b", "feat-gone",
        str(base / "wt-gone"))
    shutil.rmtree(base / "wt-gone")

    with open(repo / "file.txt", "a") as fh:
        fh.write("stashed edit\n")
    git(repo, "stash", "push", "-q", "-m", "test stash")

    print(f"Created {repo} plus worktrees wt-safe, "
          f"wt-dirty, wt-locked, wt-detached under {base} "
          f"(wt-gone's folder was deleted on purpose — "
          f"its registration is prunable)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
