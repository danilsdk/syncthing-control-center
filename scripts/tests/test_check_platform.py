"""Exercise B6 runtime validation and the native runner's failure handling."""

import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

SPEC = importlib.util.spec_from_file_location(
    "scc_check_platform", Path(__file__).parents[1] / "check_platform.py"
)
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


class PlatformChecks(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        report = patch.object(check, "report")
        self.report = report.start()
        self.addCleanup(report.stop)

    def test_wrong_runner_architecture_is_rejected(self):
        with (
            patch.object(check.platform, "system", return_value="Darwin"),
            patch.object(check.platform, "machine", return_value="x86_64"),
            self.assertRaisesRegex(RuntimeError, "Expected Darwin / arm64"),
        ):
            check.require_runtime("Darwin", "arm64")

    def test_wrong_interpreter_version_is_rejected(self):
        with (
            patch.object(check.platform, "system", return_value="Windows"),
            patch.object(check.platform, "machine", return_value="AMD64"),
            patch.object(sys, "version_info", (3, 13, 0)),
            self.assertRaisesRegex(RuntimeError, "64-bit Python 3.12"),
        ):
            check.require_runtime("Windows", "x64")

    def test_child_exit_code_and_output_are_retained(self):
        log = self.directory / "run.log"
        code = check.run_step(
            [sys.executable, "-c", "print('child output'); raise SystemExit(7)"],
            log,
            os.environ.copy(),
        )
        self.assertEqual(code, 7)
        self.assertEqual(log.read_text(encoding="utf-8").strip(), "child output")

    def test_timed_out_child_is_reaped_and_timeout_stays_a_failure(self):
        def stop(child):
            child.terminate()
            child.wait(timeout=10)

        with (
            patch.object(check, "interrupt", side_effect=stop) as interrupt,
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            check.run_step(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                self.directory / "timeout.log",
                os.environ.copy(),
                timeout=0.1,
            )
        interrupt.assert_called_once()
        self.assertIsNotNone(interrupt.call_args.args[0].returncode)

    def test_native_interrupt_waits_before_escalating(self):
        child = Mock(pid=123)
        child.wait.side_effect = [subprocess.TimeoutExpired("test child", 120), -9]
        sigkill = getattr(signal, "SIGKILL", 9)
        with (
            patch.object(os, "killpg", create=True) as kill,
            patch.object(signal, "SIGKILL", sigkill, create=True),
        ):
            check.interrupt(child)
        self.assertEqual(kill.call_args_list, [call(123, signal.SIGINT), call(123, sigkill)])
        self.assertEqual(child.wait.call_args_list, [call(timeout=120), call()])

    def test_sql_suite_requires_executed_passing_tests(self):
        path = self.directory / "sql.xml"
        for tests, skipped in [(0, 0), (1, 1), (1, 0)]:
            with self.subTest(tests=tests, skipped=skipped):
                path.write_text(
                    f'<testsuites><testsuite tests="{tests}" skipped="{skipped}" '
                    'failures="0" errors="0"/></testsuites>',
                    encoding="utf-8",
                )
                if not tests or skipped:
                    with self.assertRaisesRegex(RuntimeError, "complete SQL suite"):
                        check.check_sql_report(path)
                else:
                    check.check_sql_report(path)
        self.report.assert_called_once_with("sql: 1 SQL tests passed (0 skipped)")

    def test_live_report_requires_success_and_explicit_cleanup(self):
        path = self.directory / "live.json"
        for details in [
            {"result": "failed", "cleanup": {"running": []}},
            {"result": "passed", "cleanup": {"running": ["node-a"]}},
            {"result": "passed"},
        ]:
            with self.subTest(details=details):
                path.write_text(json.dumps(details), encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "success and cleanup"):
                    check.check_live_report(path, cleanup={"running": []})
        complete = {"result": "passed", "cleanup": {"running": []}}
        path.write_text(json.dumps(complete), encoding="utf-8")
        self.assertEqual(check.check_live_report(path, cleanup={"running": []}), complete)
