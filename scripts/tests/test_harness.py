"""Exercise scope and isolation with real, disposable Git repositories."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
import sys
from unittest.mock import patch

MODULE = Path(__file__).parents[1] / "harness.py"
SPEC = importlib.util.spec_from_file_location("scc_harness", MODULE)
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        h.git(self.root, "init", "-b", "main")
        h.git(self.root, "config", "user.name", "Harness Test")
        h.git(self.root, "config", "user.email", "harness@example.invalid")
        h.git(self.root, "remote", "add", "origin", "https://github.com/danilsdk/syncthing-control-center.git")
        h.git(self.root, "commit", "--allow-empty", "-m", "before harness")
        self.empty_base = h.git(self.root, "rev-parse", "HEAD").strip()
        self.project = {"remote": "origin", "base_branch": "main", "roles": {
            "A": {"github": "playerboom", "focus": "интеграция"},
            "B": {"github": "danilsdk", "focus": "автоматизация"}},
            "ownership": [{"path": "**", "role": "A"},
                          {"path": "backend/app/scheduling/**", "role": "B"}]}
        self.task = {"id": "B1", "role": "B", "title": "Расписания",
                     "branch": "codex/b01-scheduling", "depends_on": [],
                     "allow": ["backend/app/scheduling/**", "docs/agent-log/b/B1.md"],
                     "deny": [], "spec": "docs/spec.md", "acceptance": ["Работает"]}
        self.write_json(".harness/project.json", self.project)
        self.write_json(".harness/tasks/B1.json", self.task)
        self.write_json(".harness/merge-policy.json", {
            "schema_version": 1, "repository": "danilsdk/syncthing-control-center",
            "mode": "peer-agent", "merge_method": "squash", "required_checks": ["validate"],
            "require_up_to_date": True, "require_current_head_approval": True})
        self.write(".github/CODEOWNERS", h.codeowners_text(self.project))
        self.write(".gitignore", ".harness/local/\n")
        self.write("docs/spec.md", "# Spec\n")
        self.write("scripts/harness.py", "# Test fixture, not executed\n")
        self.write("backend/app/models/shared.py", "SCHEMA = 1\n")
        h.git(self.root, "add", ".")
        h.git(self.root, "commit", "-m", "fixture")
        h.git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")
        h.git(self.root, "switch", "-c", self.task["branch"])
        self.output = io.StringIO()

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_json(self, name, value):
        self.write(name, json.dumps(value, ensure_ascii=False) + "\n")

    def check(self, **options):
        with redirect_stdout(self.output):
            return h.check_scope(self.root, self.task, "origin/main", **options)

    def test_own_untracked_file_is_in_scope(self):
        self.write("backend/app/scheduling/engine.py", "enabled = True\n")
        self.assertIn("backend/app/scheduling/engine.py", self.check())

    def test_foreign_untracked_file_is_rejected(self):
        self.write("frontend/page.js", "x = 1\n")
        with self.assertRaises(h.HarnessError):
            self.check()

    def test_foreign_tracked_edit_is_rejected(self):
        self.write("backend/app/models/shared.py", "SCHEMA = 2\n")
        with self.assertRaises(h.HarnessError):
            self.check()

    def test_staged_mode_checks_only_commit_contents(self):
        self.write("backend/app/scheduling/engine.py", "enabled = True\n")
        self.write("backend/app/models/shared.py", "SCHEMA = 2\n")
        h.git(self.root, "add", "backend/app/scheduling/engine.py")
        self.assertEqual(self.check(staged=True), {"backend/app/scheduling/engine.py"})
        h.git(self.root, "add", "backend/app/models/shared.py")
        with self.assertRaises(h.HarnessError):
            self.check(staged=True)

    def test_foreign_committed_change_is_rejected(self):
        self.write("backend/app/models/shared.py", "SCHEMA = 2\n")
        h.git(self.root, "add", "backend/app/models/shared.py")
        h.git(self.root, "commit", "-m", "foreign change")
        with self.assertRaises(h.HarnessError):
            self.check(committed_only=True)

    def test_foreign_rename_cannot_hide_source(self):
        (self.root / "backend/app/scheduling").mkdir()
        h.git(self.root, "mv", "backend/app/models/shared.py", "backend/app/scheduling/shared.py")
        paths = h.changed_paths(self.root, self.task, "origin/main", staged=True)
        self.assertIn("backend/app/models/shared.py", paths)
        self.assertIn("backend/app/scheduling/shared.py", paths)
        with self.assertRaises(h.HarnessError):
            self.check(staged=True)

    def test_unchanged_preexisting_file_is_preserved(self):
        self.write("личный бриф.docx", "untouched")
        with redirect_stdout(self.output):
            h.snapshot(self.root, self.task)
        self.assertNotIn("личный бриф.docx", self.check())
        self.write("личный бриф.docx", "modified")
        with self.assertRaises(h.HarnessError):
            self.check()

    def test_snapshot_cannot_be_overwritten(self):
        with redirect_stdout(self.output):
            h.snapshot(self.root, self.task)
            with self.assertRaises(h.HarnessError):
                h.snapshot(self.root, self.task)

    def test_branch_must_match_task(self):
        h.git(self.root, "switch", "main")
        with self.assertRaises(h.HarnessError):
            h.resolve_task(self.root, "B1")

    def test_ci_uses_base_policy_instead_of_widened_task(self):
        widened = dict(self.task, allow=["**"])
        self.write_json(".harness/tasks/B1.json", widened)
        self.write("backend/app/models/shared.py", "SCHEMA = 3\n")
        base_task = h.resolve_task(self.root, "B1", policy_ref="origin/main")
        self.assertFalse(h.allowed(base_task, "backend/app/models/shared.py"))

    def test_path_wildcards_do_not_cross_directories(self):
        self.assertTrue(h.path_matches("backend/app/models/node.py", "backend/app/models/**"))
        self.assertFalse(h.path_matches("backend/app/models/node.py", "backend/*.py"))
        self.assertTrue(h.path_matches("a/b/c", "**"))
        self.assertFalse(h.allowed(dict(self.task, deny=["backend/app/scheduling/private/**"]),
                                   "backend/app/scheduling/private/key.py"))

    def test_owner_specific_rule_overrides_default(self):
        self.assertEqual(h.owner(self.project, "backend/app/scheduling/engine.py"), "B")
        self.assertEqual(h.owner(self.project, "backend/app/models/node.py"), "A")

    def test_start_preserves_source_worktree(self):
        h.git(self.root, "switch", "main")
        h.git(self.root, "branch", "-D", self.task["branch"])
        self.write("existing-local-file.txt", "keep me")
        destination = Path(self.temp.name) / "task-worktree"
        args = SimpleNamespace(base="origin/main", path=str(destination), no_fetch=True)
        with redirect_stdout(self.output):
            h.start(self.root, self.task, args)
        self.assertEqual(h.git(self.root, "branch", "--show-current").strip(), "main")
        self.assertEqual((self.root / "existing-local-file.txt").read_text(encoding="utf-8"), "keep me")
        self.assertEqual(h.git(destination, "branch", "--show-current").strip(), self.task["branch"])
        self.assertTrue((destination / ".harness/local/B1.json").is_file())

    def test_start_refuses_to_copy_uncommitted_harness(self):
        h.git(self.root, "rm", "scripts/harness.py")
        h.git(self.root, "commit", "-m", "remove harness")
        h.git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.write("scripts/harness.py", "# uncommitted\n")
        destination = Path(self.temp.name) / "task-worktree"
        args = SimpleNamespace(base="origin/main", path=str(destination), no_fetch=True)
        with self.assertRaises(h.HarnessError):
            h.start(self.root, self.task, args)
        self.assertFalse(destination.exists())

    def test_cycle_in_dependencies_is_rejected(self):
        second = dict(self.task, id="A0", role="A", branch="codex/a00-foundation", depends_on=["B1"])
        first = dict(self.task, depends_on=["A0"])
        self.write_json(".harness/tasks/A0.json", second)
        self.write_json(".harness/tasks/B1.json", first)
        with self.assertRaisesRegex(h.HarnessError, "Циклическая"):
            h.check_structure(self.root)

    def test_product_failure_is_a_failing_gate(self):
        self.write("frontend/package.json", "{}\n")
        self.write_json(".harness/checks/frontend.json", {
            "name": "frontend", "when_exists": ["frontend/package.json"], "cwd": ".",
            "commands": [[sys.executable, "-c", "raise SystemExit(7)"]]})
        with redirect_stdout(self.output), self.assertRaises(h.HarnessError):
            h.product_checks(self.root)

    def test_absent_product_is_explicitly_not_applicable(self):
        self.write_json(".harness/checks/backend.json", {
            "name": "backend", "when_exists": ["backend/pyproject.toml"], "cwd": ".",
            "commands": [[sys.executable, "-c", "raise SystemExit(7)"]]})
        with redirect_stdout(self.output):
            h.product_checks(self.root)
        self.assertIn("НЕ ПРИМЕНИМО", self.output.getvalue())

    def test_empty_product_gate_is_rejected(self):
        self.write_json(".harness/checks/backend.json", {
            "name": "backend", "when_exists": ["backend/pyproject.toml"], "commands": []})
        with self.assertRaisesRegex(h.HarnessError, "не должна быть пустой"):
            h.check_structure(self.root)

    def test_real_precommit_hook_blocks_foreign_commit(self):
        self.write("scripts/harness.py", MODULE.read_text(encoding="utf-8"))
        source_hook = MODULE.parent.parent / ".githooks/pre-commit"
        self.write(".githooks/pre-commit", source_hook.read_text(encoding="utf-8"))
        source_runner = MODULE.parent.parent / ".githooks/python"
        self.write(".githooks/python", source_runner.read_text(encoding="utf-8"))
        with redirect_stdout(self.output):
            h.install_hooks(self.root)
        self.write("backend/app/scheduling/engine.py", "enabled = True\n")
        h.git(self.root, "add", "backend/app/scheduling/engine.py")
        h.git(self.root, "commit", "-m", "own change")
        self.write("backend/app/models/shared.py", "SCHEMA = 2\n")
        h.git(self.root, "add", "backend/app/models/shared.py")
        with self.assertRaises(h.HarnessError):
            h.git(self.root, "commit", "-m", "foreign change")
        self.assertEqual(h.git(self.root, "log", "-1", "--format=%s").strip(), "own change")

    def test_hook_falls_back_to_python_when_python3_is_unusable(self):
        source_runner = MODULE.parent.parent / ".githooks/python"
        self.write(".githooks/python", source_runner.read_text(encoding="utf-8"))
        fake_bin = self.root / "fake-bin"
        fake_bin.mkdir()
        marker = self.root / "selected-python.txt"
        fake_python3 = fake_bin / "python3"
        fake_python3.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        fake_python3.chmod(0o755)
        fake_python = fake_bin / "python"
        fake_python.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-c\" ]; then exit 0; fi\n"
            "printf '%s\\n' \"$*\" > \"$MARKER\"\n",
            encoding="utf-8",
        )
        fake_python.chmod(0o755)
        environment = os.environ.copy()
        environment.update({"PATH": f"{fake_bin}:/usr/bin:/bin", "MARKER": str(marker)})
        result = subprocess.run(
            ["sh", str(self.root / ".githooks/python"), "scope", "--staged"],
            cwd=self.root,
            env=environment,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("scripts/harness.py scope --staged", marker.read_text(encoding="utf-8"))

    def test_trusted_validator_rejects_replaced_pr_script(self):
        self.write("scripts/harness.py", "print('all checks passed')\n")
        self.write("frontend/outside-task.txt", "foreign\n")
        h.git(self.root, "add", "scripts/harness.py", "frontend/outside-task.txt")
        h.git(self.root, "commit", "-m", "replace validator")
        replacement = subprocess.run(
            [sys.executable, "scripts/harness.py", "scope"],
            cwd=self.root,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(replacement.returncode, 0)
        with self.assertRaises(h.HarnessError):
            self.check(committed_only=True)

    def test_prepush_refuses_main_before_any_external_action(self):
        head = h.git(self.root, "rev-parse", "HEAD").strip()
        data = f"refs/heads/main {head} refs/heads/main {'0' * 40}\n"
        with patch.object(sys, "stdin", io.StringIO(data)):
            with self.assertRaisesRegex(h.HarnessError, "рабочую ветку"):
                h.pre_push(self.root)

    def commit_engine(self):
        self.write("backend/app/scheduling/engine.py", "enabled = True\n")
        h.git(self.root, "add", "backend/app/scheduling/engine.py")
        h.git(self.root, "commit", "-m", "engine")

    def plan(self, **options):
        arguments = dict(branch=self.task["branch"], base="origin/main", head="HEAD",
                         author="danilsdk", reviewer="playerboom")
        arguments.update(options)
        return h.review_plan(self.root, **arguments)

    def test_review_plan_pins_reviewed_head_base_and_peer(self):
        self.commit_engine()
        plan = self.plan()
        self.assertEqual(plan["head"], h.git(self.root, "rev-parse", "HEAD").strip())
        self.assertEqual(plan["base"], h.git(self.root, "rev-parse", "origin/main").strip())
        self.assertEqual(plan["reviewer"], "playerboom")
        self.assertEqual(plan["status"], "ready_for_substantive_review")
        self.assertFalse(plan["bootstrap"])

    def test_review_plan_rejects_self_review_and_wrong_author(self):
        self.commit_engine()
        for options in ({"reviewer": "danilsdk"}, {"author": "playerboom"},
                        {"reviewer": "someone-else"}):
            with self.subTest(options=options), self.assertRaises(h.HarnessError):
                self.plan(**options)

    def test_review_plan_uses_accepted_scope_not_pr_widening(self):
        self.write_json(".harness/tasks/B1.json", dict(self.task, allow=["**"]))
        self.write("backend/app/models/shared.py", "SCHEMA = 2\n")
        h.git(self.root, "add", ".harness/tasks/B1.json", "backend/app/models/shared.py")
        h.git(self.root, "commit", "-m", "widen scope")
        with self.assertRaisesRegex(h.HarnessError, "карточку"):
            self.plan()

    def test_review_plan_rejects_foreign_source_of_rename(self):
        (self.root / "backend/app/scheduling").mkdir()
        h.git(self.root, "mv", "backend/app/models/shared.py", "backend/app/scheduling/shared.py")
        h.git(self.root, "commit", "-m", "rename")
        with self.assertRaisesRegex(h.HarnessError, "карточку"):
            self.plan()

    def test_review_plan_rejects_outdated_base(self):
        self.commit_engine()
        h.git(self.root, "switch", "main")
        self.write("backend/app/models/shared.py", "SCHEMA = 2\n")
        h.git(self.root, "add", "backend/app/models/shared.py")
        h.git(self.root, "commit", "-m", "advance main")
        h.git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")
        h.git(self.root, "switch", self.task["branch"])
        with self.assertRaises(h.HarnessError):
            self.plan()

    def test_review_plan_rejects_different_remote(self):
        self.commit_engine()
        h.git(self.root, "remote", "set-url", "origin", "https://github.com/other/project.git")
        with self.assertRaisesRegex(h.HarnessError, "Origin"):
            self.plan()

    def test_review_plan_bootstrap_only_allows_h0(self):
        self.commit_engine()
        with self.assertRaisesRegex(h.HarnessError, "H0"):
            self.plan(base=self.empty_base)
        task = dict(self.task, id="H0", branch="codex/h00-harness", allow=["**"])
        self.write_json(".harness/tasks/H0.json", task)
        h.git(self.root, "add", ".harness/tasks/H0.json")
        h.git(self.root, "commit", "-m", "initial harness")
        self.assertTrue(self.plan(base=self.empty_base, branch=task["branch"])["bootstrap"])

    def test_review_plan_does_not_fall_back_to_pr_policy(self):
        original_policy = (self.root / ".harness/merge-policy.json").read_text(encoding="utf-8")
        h.git(self.root, "rm", ".harness/merge-policy.json")
        h.git(self.root, "commit", "-m", "no accepted merge policy")
        h.git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.commit_engine()
        self.write(".harness/merge-policy.json", original_policy)
        self.write_json(".harness/tasks/B1.json", dict(self.task, allow=["**"]))
        h.git(self.root, "add", ".harness/merge-policy.json", ".harness/tasks/B1.json")
        h.git(self.root, "commit", "-m", "unaccepted policy in PR")
        with self.assertRaises(h.HarnessError):
            self.plan()

    def test_review_plan_selects_b_as_reviewer_of_a(self):
        task = dict(self.task, id="A0", role="A", branch="codex/a00-foundation",
                    allow=["backend/app/models/**"])
        self.write_json(".harness/tasks/A0.json", task)
        h.git(self.root, "add", ".harness/tasks/A0.json")
        h.git(self.root, "commit", "-m", "accepted A0 task")
        h.git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.write("backend/app/models/shared.py", "SCHEMA = 2\n")
        h.git(self.root, "add", "backend/app/models/shared.py")
        h.git(self.root, "commit", "-m", "A0 model")
        plan = self.plan(branch=task["branch"], author="playerboom", reviewer="danilsdk")
        self.assertEqual(plan["task"], "A0")
        self.assertEqual(plan["reviewer"], "danilsdk")

    def test_empty_merge_checks_cannot_enable_peer_mode(self):
        policy = json.loads((self.root / ".harness/merge-policy.json").read_text(encoding="utf-8"))
        self.write_json(".harness/merge-policy.json", dict(policy, required_checks=[]))
        with self.assertRaises(h.HarnessError):
            h.check_structure(self.root)


if __name__ == "__main__":
    unittest.main()
