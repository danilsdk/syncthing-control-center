"""Exercise scope and isolation with real, disposable Git repositories."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import textwrap
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

    def shell(self):
        command = shutil.which("sh")
        if command:
            return command
        # Git for Windows bundles sh even when only Git/cmd is on PATH.
        command = Path(shutil.which("git")).resolve().parents[1] / "bin/sh.exe"
        self.assertTrue(command.is_file(), "Git hooks require a POSIX shell")
        return str(command)

    def program_shim(self, name, program):
        self.write(name, "#!/bin/sh\nexec " + shlex.quote(Path(program).as_posix()) + ' "$@"\n')
        path = self.root / name
        path.chmod(path.stat().st_mode | 0o111)
        return path

    def python_shim(self, name):
        return self.program_shim(name, sys.executable)

    def child_env(self, **options):
        # Console encoding is independent of repository-file decoding.
        return dict(os.environ, PYTHONUTF8="0", PYTHONIOENCODING="utf-8", **options)

    def accept_validator(self):
        self.write("scripts/harness.py", MODULE.read_text(encoding="utf-8"))
        h.git(self.root, "add", "scripts/harness.py")
        h.git(self.root, "commit", "-m", "accepted validator")
        h.git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")

    def ci_scope(self, branch=None, base="origin/main"):
        # Execute the actual workflow block, including its bootstrap branch.
        workflow = MODULE.parent.parent / ".github/workflows/harness.yml"
        lines = workflow.read_text(encoding="utf-8").splitlines()
        step = lines.index("      - name: Check task scope against base policy")
        start = lines.index("        run: |", step) + 1
        end = start
        while end < len(lines) and (not lines[end] or lines[end].startswith("          ")):
            end += 1
        script = self.root / ".harness/local/ci-scope.sh"
        self.write(str(script.relative_to(self.root)), textwrap.dedent("\n".join(lines[start:end])) + "\n")
        # This block targets Ubuntu, where setup-python supplies python3.
        # A private shim lets Windows exercise exactly the same shell commands.
        binary = self.python_shim(".harness/local/bin/python3").parent
        runner = Path(self.temp.name) / "runner scratch"
        runner.mkdir(exist_ok=True)
        # GitHub provides an exact SHA, not a ref with a slash (which MSYS
        # may rewrite as a filesystem path when passed to git cat-file).
        base_sha = h.git(self.root, "rev-parse", "--verify", base).strip()
        env = self.child_env(HARNESS_BRANCH=branch or self.task["branch"],
                             HARNESS_BASE=base_sha, RUNNER_TEMP=runner.as_posix(),
                             PATH=str(binary) + os.pathsep + os.environ.get("PATH", ""))
        return subprocess.run([self.shell(), "-eu", script.as_posix()], cwd=self.root,
                              env=env, text=True, encoding="utf-8", capture_output=True, timeout=30)

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

    def test_ci_scope_allows_an_ordinary_task_change(self):
        self.accept_validator()
        self.commit_engine()
        result = self.ci_scope()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ci_scope_rejects_replaced_validator_and_foreign_file(self):
        self.accept_validator()
        self.write("scripts/harness.py", 'print("HEAD_VALIDATOR_RAN")\n')
        self.write("ast.py", 'raise RuntimeError("HEAD_MODULE_RAN")\n')
        self.write("frontend/outside-task.txt", "foreign change\n")
        self.write_json(".harness/tasks/B1.json", dict(self.task, allow=["**"]))
        h.git(self.root, "add", "scripts/harness.py", "ast.py", "frontend/outside-task.txt", ".harness/tasks/B1.json")
        h.git(self.root, "commit", "-m", "replace validator and widen policy")
        with patch.dict(os.environ, {"PYTHONPATH": str(self.root)}):
            result = self.ci_scope()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("frontend/outside-task.txt", result.stderr)
        self.assertIn("scripts/harness.py", result.stderr)
        self.assertIn(".harness/tasks/B1.json", result.stderr)
        self.assertIn("ast.py", result.stderr)
        self.assertNotIn("HEAD_VALIDATOR_RAN", result.stdout)
        self.assertNotIn("HEAD_MODULE_RAN", result.stdout + result.stderr)

    def test_ci_scope_does_not_fall_back_to_head_when_base_validator_is_missing(self):
        h.git(self.root, "rm", "scripts/harness.py")
        h.git(self.root, "commit", "-m", "base has policy but no validator")
        h.git(self.root, "update-ref", "refs/remotes/origin/main", "HEAD")
        self.write("scripts/harness.py", 'print("HEAD_VALIDATOR_RAN")\n')
        h.git(self.root, "add", "scripts/harness.py")
        h.git(self.root, "commit", "-m", "untrusted replacement")
        result = self.ci_scope()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("HEAD_VALIDATOR_RAN", result.stdout)

    def test_ci_scope_bootstrap_allows_h0(self):
        task = dict(self.task, id="H0", branch="codex/h00-harness", allow=["**"])
        self.write_json(".harness/tasks/H0.json", task)
        self.write("scripts/harness.py", MODULE.read_text(encoding="utf-8"))
        h.git(self.root, "add", ".harness/tasks/H0.json", "scripts/harness.py")
        h.git(self.root, "commit", "-m", "bootstrap H0")
        result = self.ci_scope(branch=task["branch"], base=self.empty_base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_ci_scope_bootstrap_rejects_other_tasks(self):
        result = self.ci_scope(base=self.empty_base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("merge H0 first", result.stderr)

    def test_cli_reads_and_writes_utf8_without_utf8_mode(self):
        self.write("scripts/harness.py", MODULE.read_text(encoding="utf-8"))
        self.write_json(".harness/tasks/B1.json", dict(self.task, title="Интеграция и 雪"))
        self.write("docs/spec.md", "# Интеграция\n\n[Наблюдения](наблюдения-雪.md)\n")
        self.write("docs/наблюдения-雪.md", "# Наблюдения\n")
        self.write("backend/app/models/shared.py", '# Интеграция\nLABEL = "雪"\n')
        self.write("личный бриф-雪.docx", "untouched")
        for command in (["check"], ["snapshot", "B1"], ["codeowners"]):
            with self.subTest(command=command):
                result = subprocess.run(
                    [sys.executable, "-X", "utf8=0", "-X", "warn_default_encoding",
                     "-W", "error::EncodingWarning", "scripts/harness.py", *command],
                    cwd=self.root, env=self.child_env(), text=True, encoding="utf-8",
                    capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        snapshot = json.loads((self.root / ".harness/local/B1.json").read_text(encoding="utf-8"))
        self.assertIn("личный бриф-雪.docx", snapshot["preexisting_untracked"])

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
        self.write(".githooks/run-python.sh", (source_hook.parent / "run-python.sh").read_text(encoding="utf-8"))
        self.python_shim(".venv/bin/python")
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

    def prepare_launcher_probe(self):
        for name in ("pre-commit", "pre-push", "run-python.sh"):
            self.write(".githooks/" + name,
                       (MODULE.parent.parent / ".githooks" / name).read_text(encoding="utf-8"))
        self.write("scripts/validate.sh", (MODULE.parent / "validate.sh").read_text(encoding="utf-8"))
        self.write("scripts/harness.py", "import json, sys\n"
                   "print(json.dumps({'executable': sys.executable, 'args': sys.argv[1:], "
                   "'input': sys.stdin.read() if sys.argv[1:] == ['pre-push'] else ''}))\n")
        # No system Python, env or py launcher can be found through this PATH.
        binary = self.program_shim(".harness/local/launch bin/sh", self.shell()).parent
        dirname = subprocess.run([self.shell(), "-c", "command -v dirname"], text=True,
                                 encoding="utf-8", capture_output=True, check=True).stdout.strip()
        self.program_shim(str((binary / "dirname").relative_to(self.root)), dirname)
        return self.child_env(PATH=str(binary)), binary

    def assert_launchers(self, env, expected_python):
        for script, arguments, expected in (
                (".githooks/pre-commit", [], ["scope", "--staged"]),
                (".githooks/pre-push", [], ["pre-push"]),
                ("scripts/validate.sh", ["--task", "B1"], ["validate", "--task", "B1"])):
            with self.subTest(script=script):
                result = subprocess.run([self.shell(), script, *arguments], cwd=self.root,
                                        env=env, input="refs reach the hook\n", text=True,
                                        encoding="utf-8", capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                data = json.loads(result.stdout)
                self.assertEqual(Path(data["executable"]), expected_python)
                self.assertEqual(data["args"], expected)
                if expected == ["pre-push"]:
                    self.assertEqual(data["input"], "refs reach the hook\n")

    def test_launchers_work_with_only_python_on_path(self):
        env, binary = self.prepare_launcher_probe()
        self.python_shim(str((binary / "python").relative_to(self.root)))
        # An unusable python3 must not prevent fallback to a usable python.
        self.write(str((binary / "python3").relative_to(self.root)), "#!/bin/sh\nexit 1\n")
        (binary / "python3").chmod(0o755)
        self.assert_launchers(env, Path(sys.executable))

    def test_launchers_work_with_only_py_launcher_on_path(self):
        env, binary = self.prepare_launcher_probe()
        self.write(str((binary / "py").relative_to(self.root)),
                   '#!/bin/sh\n[ "$1" = -3 ] || exit 98\nshift\nexec '
                   + shlex.quote(Path(sys.executable).as_posix()) + ' "$@"\n')
        (binary / "py").chmod(0o755)
        self.assert_launchers(env, Path(sys.executable))

    def test_launchers_prefer_real_worktree_venv_without_python_on_path(self):
        env, binary = self.prepare_launcher_probe()
        h.run([sys.executable, "-m", "venv", "--without-pip", str(self.root / ".venv")], self.root)
        expected = self.root / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
        # This candidate must never be selected over the project environment.
        self.write(str((binary / "python3").relative_to(self.root)), "#!/bin/sh\nexit 99\n")
        (binary / "python3").chmod(0o755)
        self.assert_launchers(env, expected)

    def test_launcher_reports_missing_python(self):
        env, _ = self.prepare_launcher_probe()
        result = subprocess.run([self.shell(), ".githooks/pre-commit"], cwd=self.root,
                                env=env, text=True, encoding="utf-8", capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 127)
        self.assertIn("requires Python 3.10+", result.stderr)

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
