#!/usr/bin/env python3
"""Tests for worktree-janitor. Stdlib unittest only.

Run from the pack root:
    python3 -m unittest discover -s tests -v

The integration fixture is a REAL git repository built
with the git CLI in a temp dir (the tests mutate it
freely — only the TOOL is read-only). Every expected
verdict below was worked out by hand from the documented
rules (see README), never copied from the tool's output.

Hand-computed expectation for build_fixture():

    repo (main worktree, branch main) ............ KEEP
      branch main — default; 1 stash entry ("test
      stash") lives here but stashes are repo-wide.
    wt-safe (branch feat-done, merged into main
      with a merge commit, clean) ................ SAFE
    wt-dirty (branch feat-wip: 1 commit not in
      main + 1 unstaged change + 1 untracked file)  REVIEW
    wt-locked (branch feat-locked, clean, merged —
      it points at main's tip — but `git worktree
      lock`ed with reason "agent session running")  KEEP
    wt-detached (detached HEAD at main's tip,
      clean) ...................................... REVIEW
    wt-gone (branch feat-gone; directory deleted
      after creation, git marks it prunable) ...... SAFE

    Worktrees: 6 = 2 KEEP + 2 SAFE_TO_REMOVE + 2 REVIEW
    Branches: 8 = main, feat-merged-old,
      feat-merged-new, feat-unmerged, feat-done,
      feat-wip, feat-locked, feat-gone
    Stale (merged, last commit >= 30 days old, not
      checked out anywhere): exactly feat-merged-old
      (its tip commit is dated 2020-01-02; every other
      merged branch is either checked out or recent)
    Plan commands, in order:
      git worktree remove <wt-safe>
      git worktree prune
      git branch -d feat-merged-old
    Scan and plan both exit 1; findings = 2 + 2 + 1 = 5.

Read-only expectations: a scan + plan run leaves the
repo state (worktree list, branch list, stash list,
per-worktree status, and every working-tree file)
EXACTLY equal, and every subprocess argv the tool issues
passes the read-only allowlist (asserted by recording
them all).
"""
import contextlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PACK_ROOT = Path(__file__).resolve().parent.parent
SRC = PACK_ROOT / "src"
sys.path.insert(0, str(SRC))

import worktree_janitor as wj  # noqa: E402

GIT = shutil.which("git")
OLD_DATE = "2020-01-02T03:04:05 +0000"


def git(cwd, *args, date=None, check=True):
    env = dict(os.environ)
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    proc = subprocess.run(
        [GIT, "-C", str(cwd), *args],
        capture_output=True, text=True, env=env)
    if check and proc.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed: {proc.stderr}")
    return proc


def init_repo(path):
    proc = subprocess.run(
        [GIT, "init", "-q", "-b", "main", str(path)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        subprocess.run([GIT, "init", "-q", str(path)],
                       check=True, capture_output=True)
        git(path, "symbolic-ref", "HEAD",
            "refs/heads/main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test User")
    git(path, "config", "commit.gpgsign", "false")
    return path


def commit_all(repo, message, date=None):
    f = Path(repo) / "file.txt"
    with open(f, "a") as fh:
        fh.write(message + "\n")
    git(repo, "add", "file.txt")
    git(repo, "commit", "-q", "-m", message, date=date)


def build_fixture(base):
    """The repo described in this file's header."""
    repo = init_repo(base / "repo")
    commit_all(repo, "base")
    # feat-merged-old: merged, tip dated 2020 -> stale.
    git(repo, "checkout", "-q", "-b", "feat-merged-old")
    commit_all(repo, "old work", date=OLD_DATE)
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge old",
        "feat-merged-old")
    # feat-merged-new: merged, recent -> not stale.
    git(repo, "checkout", "-q", "-b", "feat-merged-new")
    commit_all(repo, "new work")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge new",
        "feat-merged-new")
    # feat-unmerged: one commit main does not have.
    git(repo, "checkout", "-q", "-b", "feat-unmerged")
    commit_all(repo, "unmerged work")
    git(repo, "checkout", "-q", "main")
    # feat-done: merged, then checked out in wt-safe.
    git(repo, "checkout", "-q", "-b", "feat-done")
    commit_all(repo, "done work")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge done",
        "feat-done")
    wt_safe = base / "wt-safe"
    git(repo, "worktree", "add", "-q", str(wt_safe),
        "feat-done")
    # wt-dirty: unmerged branch + dirty + untracked.
    wt_dirty = base / "wt-dirty"
    git(repo, "worktree", "add", "-q", "-b", "feat-wip",
        str(wt_dirty))
    commit_all(wt_dirty, "wip work")
    with open(wt_dirty / "file.txt", "a") as fh:
        fh.write("uncommitted edit\n")
    (wt_dirty / "untracked.txt").write_text("loose\n")
    # wt-locked: clean, at main's tip, but locked.
    wt_locked = base / "wt-locked"
    git(repo, "worktree", "add", "-q", "-b", "feat-locked",
        str(wt_locked))
    git(repo, "worktree", "lock", "--reason",
        "agent session running", str(wt_locked))
    # wt-detached: detached HEAD at main's tip.
    wt_detached = base / "wt-detached"
    git(repo, "worktree", "add", "-q", "--detach",
        str(wt_detached))
    # wt-gone: created, then its directory is deleted.
    wt_gone = base / "wt-gone"
    git(repo, "worktree", "add", "-q", "-b", "feat-gone",
        str(wt_gone))
    shutil.rmtree(wt_gone)
    # One stash entry, made in the main worktree.
    with open(repo / "file.txt", "a") as fh:
        fh.write("stashed edit\n")
    git(repo, "stash", "push", "-q", "-m", "test stash")
    return {"repo": repo, "wt_safe": wt_safe,
            "wt_dirty": wt_dirty, "wt_locked": wt_locked,
            "wt_detached": wt_detached, "wt_gone": wt_gone}


def repo_state(base, paths):
    """Everything a read-only tool must leave untouched:
    git's own listings plus every working-tree file."""
    repo = paths["repo"]
    state = {
        "worktrees": git(repo, "worktree", "list",
                         "--porcelain").stdout,
        "branches": git(repo, "branch", "--format="
                        "%(refname:short)|%(objectname)"
                        ).stdout,
        "stash": git(repo, "stash", "list").stdout,
        "files": {},
        "status": {},
    }
    for key in ("repo", "wt_safe", "wt_dirty", "wt_locked",
                "wt_detached"):
        root = paths[key]
        state["status"][key] = git(
            root, "status", "--porcelain=v1",
            "--branch").stdout
        for p in sorted(root.rglob("*")):
            if p.is_file() and ".git" not in p.parts:
                state["files"][str(p.relative_to(base))] = \
                    p.read_bytes()
    return state


@unittest.skipUnless(GIT, "git is not available")
class FixtureTest(unittest.TestCase):
    """Verdicts on the hand-computed fixture."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls._tmp.name)
        cls.paths = build_fixture(cls.base)
        cls.result = wj.scan(cls.paths["repo"], 30)
        cls.wts = {Path(w["path"]).name: w
                   for w in cls.result["worktrees"]}
        cls.branches = {b["name"]: b
                        for b in cls.result["branches"]}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_summary_counts(self):
        s = self.result["summary"]
        self.assertEqual(s["worktrees"], 6)
        self.assertEqual(s["keep"], 2)
        self.assertEqual(s["safe_to_remove"], 2)
        self.assertEqual(s["review"], 2)
        self.assertEqual(s["branches"], 8)
        self.assertEqual(s["stale_branches"], 1)
        self.assertEqual(s["stashes"], 1)
        self.assertEqual(s["findings"], 5)
        self.assertFalse(self.result["clean"])

    def test_default_branch(self):
        self.assertEqual(self.result["default_branch"],
                         "main")
        self.assertIn("main worktree",
                      self.result["default_branch_basis"])

    def test_main_worktree_kept(self):
        w = self.wts["repo"]
        self.assertEqual(w["verdict"], wj.KEEP)
        self.assertTrue(w["is_main"])
        self.assertEqual(w["branch"], "main")
        self.assertEqual(w["changed_files"], 0)
        self.assertEqual(w["untracked_files"], 0)

    def test_safe_worktree(self):
        w = self.wts["wt-safe"]
        self.assertEqual(w["verdict"], wj.SAFE)
        self.assertEqual(w["branch"], "feat-done")
        self.assertEqual(w["changed_files"], 0)
        self.assertFalse(w["locked"])
        self.assertFalse(w["prunable"])
        self.assertIn("merged into main",
                      w["reasons"][0])

    def test_dirty_worktree_review_with_reasons(self):
        w = self.wts["wt-dirty"]
        self.assertEqual(w["verdict"], wj.REVIEW)
        self.assertEqual(w["changed_files"], 1)
        self.assertEqual(w["staged_files"], 0)
        self.assertEqual(w["unstaged_files"], 1)
        self.assertEqual(w["untracked_files"], 1)
        joined = "; ".join(w["reasons"])
        self.assertIn("uncommitted change", joined)
        self.assertIn("untracked", joined)
        self.assertIn("not merged into main", joined)

    def test_locked_worktree_kept(self):
        w = self.wts["wt-locked"]
        self.assertEqual(w["verdict"], wj.KEEP)
        self.assertTrue(w["locked"])
        self.assertEqual(w["lock_reason"],
                         "agent session running")
        self.assertIn("locked", w["reasons"][0])

    def test_detached_worktree_review(self):
        w = self.wts["wt-detached"]
        self.assertEqual(w["verdict"], wj.REVIEW)
        self.assertTrue(w["detached"])
        self.assertIsNone(w["branch"])
        self.assertIn("detached HEAD",
                      "; ".join(w["reasons"]))

    def test_prunable_worktree_safe(self):
        w = self.wts["wt-gone"]
        self.assertEqual(w["verdict"], wj.SAFE)
        self.assertTrue(w["prunable"])
        self.assertEqual(w["status"], "missing")
        self.assertIn("prunable", w["reasons"][0])

    def test_branch_records(self):
        old = self.branches["feat-merged-old"]
        self.assertTrue(old["merged"])
        self.assertIsNone(old["checked_out_in"])
        self.assertGreaterEqual(old["age_days"], 1000)
        self.assertTrue(old["stale"])
        new = self.branches["feat-merged-new"]
        self.assertTrue(new["merged"])
        self.assertFalse(new["stale"])
        un = self.branches["feat-unmerged"]
        self.assertFalse(un["merged"])
        self.assertEqual(un["commits_not_in_default"], 1)
        self.assertEqual(un["unpushed_commits"], 1)
        self.assertIn("no upstream", un["unpushed_basis"])
        done = self.branches["feat-done"]
        self.assertEqual(done["checked_out_in"],
                         str(self.paths["wt_safe"]))
        self.assertFalse(done["stale"])
        gone = self.branches["feat-gone"]
        self.assertEqual(gone["checked_out_in"],
                         str(self.paths["wt_gone"]))
        self.assertEqual(self.result["stale_branches"],
                         ["feat-merged-old"])

    def test_stash_recorded(self):
        self.assertEqual(self.result["stash"]["count"], 1)
        self.assertIn("test stash",
                      self.result["stash"]["entries"][0])

    def test_plan_commands_exact(self):
        plan = wj.build_plan(self.result)
        self.assertEqual(
            [p["command"] for p in plan],
            ["git worktree remove "
             + shlex.quote(str(self.paths["wt_safe"])),
             "git worktree prune",
             "git branch -d feat-merged-old"])
        self.assertEqual([p["kind"] for p in plan],
                         ["worktree-remove",
                          "worktree-prune", "branch-delete"])

    def test_stale_days_threshold_moves(self):
        huge = wj.scan(self.paths["repo"], 100000)
        self.assertEqual(huge["stale_branches"], [])
        zero = wj.scan(self.paths["repo"], 0)
        self.assertEqual(set(zero["stale_branches"]),
                         {"feat-merged-old",
                          "feat-merged-new"})

    def test_scan_from_inside_safe_worktree_keeps_it(self):
        result = wj.scan(self.paths["wt_safe"], 30)
        w = {Path(x["path"]).name: x
             for x in result["worktrees"]}["wt-safe"]
        self.assertEqual(w["verdict"], wj.KEEP)
        self.assertIn("run from", "; ".join(w["reasons"]))
        self.assertEqual(
            result["summary"]["safe_to_remove"], 1)

    def test_scan_leaves_repo_state_untouched(self):
        before = repo_state(self.base, self.paths)
        wj.scan(self.paths["repo"], 30)
        wj.build_plan(self.result)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            wj.main(["plan", str(self.paths["repo"])])
        after = repo_state(self.base, self.paths)
        self.assertEqual(before, after)

    def test_every_git_call_is_allowlisted(self):
        recorded = []
        real_run = subprocess.run

        def spy(argv, *a, **k):
            recorded.append(list(argv))
            return real_run(argv, *a, **k)

        with mock.patch.object(wj.subprocess, "run", spy):
            result = wj.scan(self.paths["repo"], 30)
            wj.build_plan(result)
        self.assertTrue(recorded)
        for argv in recorded:
            self.assertEqual(argv[1], "-C")
            verb = argv[3]
            self.assertIn(verb, wj._ALLOWED_VERBS)
            if verb in ("worktree", "stash"):
                self.assertEqual(argv[4], "list")
            for tok in argv[3:]:
                self.assertNotIn(tok, wj._FORBIDDEN_TOKENS)

    def test_cli_exit_codes_and_text(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = wj.main(["scan", str(self.paths["repo"])])
        self.assertEqual(code, 1)
        self.assertIn("SAFE_TO_REMOVE", out.getvalue())
        self.assertIn("wt-dirty", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = wj.main(["plan", str(self.paths["repo"])])
        self.assertEqual(code, 1)
        self.assertIn("git branch -d feat-merged-old",
                      out.getvalue())
        self.assertIn("does NOT run them",
                      out.getvalue())

    def test_cli_json_round_trip(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = wj.main(
                ["scan", str(self.paths["repo"]), "--json"])
        self.assertEqual(code, 1)
        data = json.loads(out.getvalue())
        self.assertEqual(data["tool"], "worktree-janitor")
        self.assertEqual(data["summary"]["findings"], 5)
        self.assertEqual(data["stale_branches"],
                         ["feat-merged-old"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = wj.main(
                ["plan", str(self.paths["repo"]), "--json"])
        self.assertEqual(code, 1)
        data = json.loads(out.getvalue())
        self.assertEqual(len(data["plan"]), 3)
        self.assertEqual(data["plan"][1]["command"],
                         "git worktree prune")


@unittest.skipUnless(GIT, "git is not available")
class UpstreamTest(unittest.TestCase):
    """A branch with a real (local) upstream: unpushed
    counts come from the upstream, not the default
    branch."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        cls.origin = base / "origin.git"
        subprocess.run([GIT, "init", "-q", "--bare",
                        str(cls.origin)], check=True,
                       capture_output=True)
        cls.repo = init_repo(base / "repo")
        commit_all(cls.repo, "base")
        git(cls.repo, "remote", "add", "origin",
            str(cls.origin))
        git(cls.repo, "push", "-q", "-u", "origin", "main")
        git(cls.repo, "checkout", "-q", "-b", "feat-up")
        commit_all(cls.repo, "pushed work")
        git(cls.repo, "push", "-q", "-u", "origin",
            "feat-up")
        commit_all(cls.repo, "unpushed work")
        git(cls.repo, "checkout", "-q", "main")
        cls.wt = base / "wt-up"
        git(cls.repo, "worktree", "add", "-q",
            str(cls.wt), "feat-up")
        cls.result = wj.scan(cls.repo, 30)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_branch_unpushed_vs_upstream(self):
        b = {x["name"]: x
             for x in self.result["branches"]}["feat-up"]
        self.assertEqual(b["upstream"], "origin/feat-up")
        self.assertEqual(b["unpushed_commits"], 1)
        self.assertEqual(b["unpushed_basis"],
                         "vs upstream origin/feat-up")
        self.assertFalse(b["merged"])

    def test_worktree_review_for_unpushed(self):
        w = {Path(x["path"]).name: x
             for x in self.result["worktrees"]}["wt-up"]
        self.assertEqual(w["verdict"], wj.REVIEW)
        self.assertEqual(w["upstream"], "origin/feat-up")
        self.assertEqual(w["ahead"], 1)
        self.assertEqual(w["behind"], 0)
        self.assertIn("1 unpushed commit(s) (vs upstream "
                      "origin/feat-up)",
                      "; ".join(w["reasons"]))


@unittest.skipUnless(GIT, "git is not available")
class SmallReposTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_clean_single_worktree_repo(self):
        repo = init_repo(self.base / "repo")
        commit_all(repo, "only commit")
        result = wj.scan(repo, 30)
        self.assertTrue(result["clean"])
        self.assertEqual(result["summary"]["keep"], 1)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = wj.main(["scan", str(repo)])
        self.assertEqual(code, 0)
        self.assertIn("CLEAN", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = wj.main(["plan", str(repo)])
        self.assertEqual(code, 0)
        self.assertIn("NOTHING TO DO", out.getvalue())

    def test_empty_repo_no_commits(self):
        repo = init_repo(self.base / "repo")
        result = wj.scan(repo, 30)
        self.assertEqual(result["branches"], [])
        w = result["worktrees"][0]
        self.assertEqual(w["verdict"], wj.KEEP)
        self.assertIsNone(w["last_commit"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = wj.main(
                ["scan", str(repo), "--json"])
        self.assertEqual(code, 0)
        json.loads(out.getvalue())

    def test_bare_repo(self):
        bare = self.base / "bare.git"
        subprocess.run([GIT, "init", "-q", "--bare",
                        str(bare)], check=True,
                       capture_output=True)
        result = wj.scan(bare, 30)
        w = result["worktrees"][0]
        self.assertTrue(w["bare"])
        self.assertEqual(w["verdict"], wj.KEEP)
        self.assertEqual(w["status"], "bare")

    def test_not_a_repo_is_clean_error(self):
        plain = self.base / "plain"
        plain.mkdir()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = wj.main(["scan", str(plain)])
        self.assertEqual(code, 2)
        self.assertIn("ERROR", out.getvalue())
        self.assertNotIn("Traceback", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = wj.main(["scan", str(plain), "--json"])
        self.assertEqual(code, 2)
        data = json.loads(out.getvalue())
        self.assertIn("error", data)

    def test_missing_folder_is_clean_error(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = wj.main(
                ["scan", str(self.base / "nope"), "--json"])
        self.assertEqual(code, 2)
        self.assertIn("no such folder",
                      json.loads(out.getvalue())["error"])

    def test_git_missing_is_clean_error(self):
        repo = init_repo(self.base / "repo")
        commit_all(repo, "x")
        out = io.StringIO()
        with mock.patch("shutil.which", return_value=None):
            with contextlib.redirect_stdout(out):
                code = wj.main(["scan", str(repo)])
        self.assertEqual(code, 2)
        self.assertIn("git was not found", out.getvalue())

    def test_negative_stale_days_rejected(self):
        repo = init_repo(self.base / "repo")
        commit_all(repo, "x")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = wj.main(["scan", str(repo),
                            "--stale-days", "-1"])
        self.assertEqual(code, 2)

    def test_json_argparse_error_is_json(self):
        repo = init_repo(self.base / "repo")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                wj.main(["scan", str(repo), "--json",
                         "--bogus-flag"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn('"error"', out.getvalue())
        self.assertNotIn("Traceback", out.getvalue())


@unittest.skipUnless(GIT, "git is not available")
class AfterPruneTest(unittest.TestCase):
    """A merged, old branch checked out only in a
    worktree whose directory was deleted: not stale yet
    (still registered), but the plan must sequence its
    deletion after the prune."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        cls.repo = init_repo(base / "repo")
        commit_all(cls.repo, "base")
        git(cls.repo, "checkout", "-q", "-b",
            "feat-old-gone")
        commit_all(cls.repo, "old work", date=OLD_DATE)
        git(cls.repo, "checkout", "-q", "main")
        git(cls.repo, "merge", "-q", "--no-ff", "-m",
            "merge old gone", "feat-old-gone")
        wt = base / "wt-dead"
        git(cls.repo, "worktree", "add", "-q", str(wt),
            "feat-old-gone")
        shutil.rmtree(wt)
        cls.result = wj.scan(cls.repo, 30)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_branch_flagged_not_stale(self):
        b = {x["name"]: x
             for x in self.result["branches"]}[
                 "feat-old-gone"]
        self.assertFalse(b["stale"])
        self.assertTrue(b["stale_after_prune"])
        self.assertEqual(self.result["stale_after_prune"],
                         ["feat-old-gone"])
        self.assertEqual(self.result["stale_branches"],
                         [])

    def test_plan_sequences_delete_after_prune(self):
        plan = wj.build_plan(self.result)
        self.assertEqual(
            [(p["kind"], p["command"]) for p in plan],
            [("worktree-prune", "git worktree prune"),
             ("branch-delete-after-prune",
              "git branch -d feat-old-gone")])


class ParserTest(unittest.TestCase):
    """Pure parsers, pinned on synthetic porcelain."""

    def test_worktree_list_all_shapes(self):
        text = (
            "worktree /repo\n"
            "HEAD aaaa\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /det\n"
            "HEAD bbbb\n"
            "detached\n"
            "\n"
            "worktree /bare.git\n"
            "bare\n"
            "\n"
            "worktree /lock\n"
            "HEAD cccc\n"
            "branch refs/heads/feat\n"
            "locked agent session running now\n"
            "\n"
            "worktree /gone\n"
            "HEAD dddd\n"
            "branch refs/heads/old\n"
            "prunable gitdir file points to non-existent "
            "location\n")
        wts = wj.parse_worktree_list(text)
        self.assertEqual(len(wts), 5)
        self.assertEqual(wts[0]["branch"], "main")
        self.assertTrue(wts[1]["detached"])
        self.assertIsNone(wts[1]["branch"])
        self.assertTrue(wts[2]["bare"])
        self.assertTrue(wts[3]["locked"])
        self.assertEqual(wts[3]["lock_reason"],
                         "agent session running now")
        self.assertTrue(wts[4]["prunable"])
        self.assertIn("non-existent",
                      wts[4]["prunable_reason"])

    def test_status_with_upstream_and_changes(self):
        text = ("## main...origin/main [ahead 1, "
                "behind 2]\n"
                " M a.txt\n"
                "M  b.txt\n"
                "A  d.txt\n"
                "?? c.txt\n")
        st = wj.parse_status(text)
        self.assertEqual(st["branch"], "main")
        self.assertEqual(st["upstream"], "origin/main")
        self.assertEqual(st["ahead"], 1)
        self.assertEqual(st["behind"], 2)
        self.assertEqual(st["changed"], 3)
        self.assertEqual(st["staged"], 2)
        self.assertEqual(st["unstaged"], 1)
        self.assertEqual(st["untracked"], 1)

    def test_status_detached_and_fresh(self):
        st = wj.parse_status("## HEAD (no branch)\n")
        self.assertTrue(st["detached"])
        st = wj.parse_status("## No commits yet on dev\n")
        self.assertTrue(st["no_commits"])
        self.assertEqual(st["branch"], "dev")

    def test_status_dotted_slashed_branch(self):
        st = wj.parse_status(
            "## release/1.2...origin/release/1.2 "
            "[behind 3]\n")
        self.assertEqual(st["branch"], "release/1.2")
        self.assertEqual(st["upstream"],
                         "origin/release/1.2")
        self.assertEqual(st["ahead"], 0)
        self.assertEqual(st["behind"], 3)

    def test_status_upstream_no_divergence(self):
        st = wj.parse_status("## feat...origin/feat\n")
        self.assertEqual(st["ahead"], 0)
        self.assertEqual(st["behind"], 0)
        st = wj.parse_status("## feat\n M x\n")
        self.assertIsNone(st["upstream"])
        self.assertIsNone(st["ahead"])
        self.assertEqual(st["changed"], 1)

    def test_read_only_gate(self):
        for bad in (["worktree", "remove", "/x"],
                    ["worktree", "prune"],
                    ["branch", "-d", "x"],
                    ["branch", "-D", "x"],
                    ["checkout", "main"],
                    ["stash", "push"],
                    ["log", "--force"],
                    ["status", "--remove"]):
            with self.assertRaises(AssertionError):
                wj._assert_read_only(bad)
        for good in (["worktree", "list", "--porcelain"],
                     ["status", "--porcelain=v1",
                      "--branch"],
                     ["branch", "--merged", "main",
                      "--format=%(refname:short)"],
                     ["log", "-1", "--format=%H", "main"],
                     ["stash", "list"]):
            wj._assert_read_only(good)  # no raise

    def test_source_has_one_subprocess_call_site(self):
        src = (SRC / "worktree_janitor.py").read_text()
        self.assertEqual(src.count("subprocess.run"), 1)
        self.assertNotIn("os.system", src)
        self.assertNotIn("Popen", src)
        self.assertNotIn('"remove",', src)


class HumanSizeTest(unittest.TestCase):
    """The human byte format, pinned — it must never
    drift between runs or versions."""

    def test_human_size_pinned(self):
        cases = [(0, "0 B"), (512, "512 B"),
                 (1023, "1023 B"), (1024, "1.0 KB"),
                 (1536, "1.5 KB"), (1048576, "1.0 MB"),
                 (2621440, "2.5 MB"),
                 (1073741824, "1.0 GB"), (None, "unknown")]
        for n, want in cases:
            self.assertEqual(wj.human_size(n), want,
                             f"human_size({n})")


class MeasureTreeTest(unittest.TestCase):
    """_measure_tree on purpose-built trees — the exact
    bytes are known because the test writes every file
    itself. No git involved."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel, n):
        p = self.base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * n)
        return p

    def test_exact_bytes_known_tree(self):
        self._write("a.bin", 1000)
        self._write("sub/b.bin", 24)
        self._write("sub/deep/c.bin", 0)
        total, warnings = wj._measure_tree(self.base)
        # Regular files only: directories contribute 0.
        self.assertEqual(total, 1024)
        self.assertEqual(warnings, [])

    def test_excluded_directory_not_counted(self):
        self._write("keep.bin", 10)
        self._write("skip/skip.bin", 500)
        total, warnings = wj._measure_tree(
            self.base, exclude=[self.base / "skip"])
        self.assertEqual(total, 10)
        self.assertEqual(warnings, [])

    def test_symlinks_never_followed(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "big.bin").write_bytes(b"x" * 5000)
        tree = self.base / "tree"
        tree.mkdir()
        (tree / "real.bin").write_bytes(b"x" * 100)
        os.symlink(outside / "big.bin", tree / "file-link")
        os.symlink(outside, tree / "dir-link")
        os.symlink(tree / "no-such-target",
                   tree / "broken-link")
        total, warnings = wj._measure_tree(tree)
        # The 5,000-byte target is never counted through
        # either link; the broken link is ignored, and
        # nothing crashes.
        self.assertEqual(total, 100)
        self.assertEqual(warnings, [])

    @unittest.skipIf(
        getattr(os, "geteuid", lambda: 1)() == 0,
        "root ignores permission bits")
    def test_permission_error_warns_not_crashes(self):
        self._write("open.bin", 50)
        locked = self.base / "locked"
        locked.mkdir()
        (locked / "hidden.bin").write_bytes(b"x" * 100)
        os.chmod(locked, 0o000)
        try:
            total, warnings = wj._measure_tree(self.base)
        finally:
            os.chmod(locked, 0o755)
        # The readable part is counted exactly; the
        # unreadable directory is a named warning, and
        # its 100 bytes are absent — a lower bound.
        self.assertEqual(total, 50)
        self.assertTrue(warnings)
        self.assertIn("locked", "; ".join(warnings))

    def test_unreadable_dir_warns_not_crashes(self):
        # The same OSError path, forced via a patched
        # os.scandir so it is exercised even where the
        # suite runs as root (where chmod 0o000 does not
        # deny anything and the test above skips).
        self._write("open.bin", 50)
        locked = self.base / "locked"
        locked.mkdir()
        (locked / "hidden.bin").write_bytes(b"x" * 100)
        real_scandir = os.scandir

        def flaky(path, *a, **k):
            if os.path.normpath(str(path)) == \
                    os.path.normpath(str(locked)):
                raise PermissionError(
                    13, "Permission denied")
            return real_scandir(path, *a, **k)

        with mock.patch("os.scandir", flaky):
            total, warnings = wj._measure_tree(self.base)
        self.assertEqual(total, 50)
        self.assertTrue(warnings)
        self.assertIn("locked", "; ".join(warnings))


class GitDirsTest(unittest.TestCase):
    """_git_dirs on synthetic .git shapes — plain file
    reads, no git repo needed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_plain_git_dir(self):
        wt = self.base / "repo"
        (wt / ".git").mkdir(parents=True)
        admin, common = wj._git_dirs(wt)
        self.assertEqual(admin, wt / ".git")
        self.assertEqual(common, wt / ".git")

    def test_commondir_redirect(self):
        wt = self.base / "repo"
        gd = wt / ".git"
        gd.mkdir(parents=True)
        real = self.base / "elsewhere.git"
        real.mkdir()
        (gd / "commondir").write_text("../../elsewhere.git\n")
        admin, common = wj._git_dirs(wt)
        self.assertEqual(admin, gd)
        self.assertEqual(common, real)

    def test_linked_gitdir_file(self):
        common = self.base / "repo" / ".git"
        admin = common / "worktrees" / "wt-x"
        admin.mkdir(parents=True)
        wt = self.base / "wt-x"
        wt.mkdir()
        (wt / ".git").write_text(f"gitdir: {admin}\n")
        got_admin, got_common = wj._git_dirs(wt)
        self.assertEqual(got_admin, admin)
        self.assertEqual(got_common, common)

    def test_garbage_git_file_is_none_not_crash(self):
        wt = self.base / "wt-y"
        wt.mkdir()
        (wt / ".git").write_text("not a gitdir pointer\n")
        self.assertEqual(wj._git_dirs(wt), (None, None))
        empty = self.base / "wt-z"
        empty.mkdir()
        self.assertEqual(wj._git_dirs(empty), (None, None))


@unittest.skipUnless(GIT, "git is not available")
class DiskAttributionTest(unittest.TestCase):
    """Disk records on a small hand-built repo: the
    working-tree contents are exact bytes the test
    wrote itself."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        cls.base = base
        cls.repo = init_repo(base / "repo")
        (cls.repo / "file.txt").write_bytes(
            b"x" * 99 + b"\n")  # exactly 100 bytes
        git(cls.repo, "add", "file.txt")
        git(cls.repo, "commit", "-q", "-m", "base")
        cls.wt = base / "wt-sized"
        git(cls.repo, "worktree", "add", "-q", "-b",
            "feat-sized", str(cls.wt))
        (cls.wt / "payload.bin").write_bytes(b"y" * 2000)
        sub = cls.wt / "sub"
        sub.mkdir()
        (sub / "payload2.bin").write_bytes(b"z" * 3000)
        cls.result = wj.scan(cls.repo, 30)
        cls.recs = {Path(w["path"]).name: w
                    for w in cls.result["worktrees"]}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_worktree_bytes_exact(self):
        w = self.recs["wt-sized"]
        # file.txt (100) + payload.bin (2000) +
        # sub/payload2.bin (3000) + the .git pointer
        # file (a regular file — its own bytes are part
        # of the working tree).
        expected = 100 + 2000 + 3000 + os.path.getsize(
            self.wt / ".git")
        self.assertEqual(w["disk"]["worktree_bytes"],
                         expected)
        self.assertGreater(w["disk"]["metadata_bytes"], 0)
        self.assertEqual(
            w["disk"]["total_bytes"],
            w["disk"]["worktree_bytes"]
            + w["disk"]["metadata_bytes"])
        self.assertTrue(w["disk"]["complete"])
        self.assertEqual(w["disk"]["warnings"], [])

    def test_main_worktree_git_dir_not_double_counted(self):
        w = self.recs["repo"]
        # The working tree holds exactly file.txt; the
        # whole .git directory is metadata, counted
        # once, on the metadata side only.
        self.assertEqual(w["disk"]["worktree_bytes"], 100)
        self.assertGreater(w["disk"]["metadata_bytes"], 0)

    def test_totals_add_up(self):
        total = self.result["disk"]["total_bytes"]
        self.assertEqual(
            total,
            sum(w["disk"]["total_bytes"]
                for w in self.result["worktrees"]))
        # Nothing in this fixture is SAFE (the sized
        # worktree has untracked payloads -> REVIEW), so
        # the reclaimable figure is exactly 0.
        self.assertEqual(self.result["summary"]
                         ["safe_to_remove"], 0)
        self.assertEqual(
            self.result["disk"]["reclaimable_bytes"], 0)
        self.assertTrue(self.result["disk"]["complete"])

    def test_json_shape_raw_bytes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = wj.main(
                ["scan", str(self.repo), "--json"])
        self.assertEqual(code, 1)
        data = json.loads(out.getvalue())
        w = {Path(x["path"]).name: x
             for x in data["worktrees"]}["wt-sized"]
        for key in ("worktree_bytes", "metadata_bytes",
                    "total_bytes"):
            self.assertIsInstance(w["disk"][key], int)
        self.assertIsInstance(w["disk"]["complete"], bool)
        self.assertEqual(w["disk"]["warnings"], [])
        for key in ("total_bytes", "reclaimable_bytes",
                    "complete"):
            self.assertIn(key, data["disk"])

    def test_text_output_shows_disk(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            wj.main(["scan", str(self.repo)])
        text = out.getvalue()
        w = self.recs["wt-sized"]
        self.assertIn(
            "disk " + wj.human_size(
                w["disk"]["total_bytes"]), text)
        self.assertIn("reclaimable", text)


@unittest.skipUnless(GIT, "git is not available")
class DiskFixtureTest(unittest.TestCase):
    """Disk records on the big hand-computed fixture:
    the gone worktree measures as null, and the
    reclaimable total is exactly the SAFE worktree's
    total (wt-safe is the only SAFE one with a
    directory still on disk)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls._tmp.name)
        cls.paths = build_fixture(cls.base)
        cls.result = wj.scan(cls.paths["repo"], 30)
        cls.wts = {Path(w["path"]).name: w
                   for w in cls.result["worktrees"]}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_missing_worktree_disk_is_null(self):
        self.assertIsNone(self.wts["wt-gone"]["disk"])
        for name in ("repo", "wt-safe", "wt-dirty",
                     "wt-locked", "wt-detached"):
            dk = self.wts[name]["disk"]
            self.assertIsNotNone(dk, name)
            self.assertIsInstance(dk["total_bytes"], int)
            self.assertGreater(dk["total_bytes"], 0)

    def test_reclaimable_is_safe_total_only(self):
        self.assertEqual(
            self.result["disk"]["reclaimable_bytes"],
            self.wts["wt-safe"]["disk"]["total_bytes"])
        self.assertEqual(
            self.result["disk"]["total_bytes"],
            sum(w["disk"]["total_bytes"]
                for w in self.result["worktrees"]
                if w["disk"] is not None))

    def test_plan_remove_reason_names_reclaim(self):
        plan = wj.build_plan(self.result)
        remove = [p for p in plan
                  if p["kind"] == "worktree-remove"]
        self.assertEqual(len(remove), 1)
        self.assertIn("reclaims", remove[0]["reason"])
        self.assertIn(
            wj.human_size(
                self.wts["wt-safe"]["disk"]
                ["total_bytes"]),
            remove[0]["reason"])


if __name__ == "__main__":
    unittest.main()
