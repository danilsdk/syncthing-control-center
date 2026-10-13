"""Exercise scope and isolation with real, disposable Git repositories."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
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
        path.write_text(content)

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
        self.assertEqual((self.root / "existing-local-file.txt").read_text(), "keep me")
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
        self.write("scripts/harness.py", MODULE.read_text())
        source_hook = MODULE.parent.parent / ".githooks/pre-commit"
        self.write(".githooks/pre-commit", source_hook.read_text())
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

    def test_prepush_refuses_main_before_any_external_action(self):
        head = h.git(self.root, "rev-parse", "HEAD").strip()
        data = f"refs/heads/main {head} refs/heads/main {'0' * 40}\n"
        with patch.object(sys, "stdin", io.StringIO(data)):
            with self.assertRaisesRegex(h.HarnessError, "рабочую ветку"):
                h.pre_push(self.root)


if __name__ == "__main__":
    unittest.main()
