from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import subprocess
import unittest
from unittest.mock import ANY, patch

from click.testing import CliRunner

from merdag.cli import main
from merdag.watch import watch_plan


class Stage4WatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_cli_help_lists_watch(self) -> None:
        result = self.runner.invoke(main, ["--help"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("watch", result.output)

    def test_watch_command_wires_cli_options(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with self.runner.isolated_filesystem(temp_dir=tmpdir):
                self.runner.invoke(main, ["init", "test"], catch_exceptions=False)
                with patch("merdag.cli.watch_plan") as watch_mock:
                    result = self.runner.invoke(
                        main,
                        ["watch", "--tier", "FAST", "--on-ready", "python -c \"print('handled')\""],
                        catch_exceptions=False,
                    )

        self.assertEqual(result.exit_code, 0)
        watch_mock.assert_called_once()
        self.assertEqual(watch_mock.call_args.args[0], Path("plan.mermaid"))
        self.assertEqual(
            watch_mock.call_args.kwargs,
            {
                "on_ready": "python -c \"print('handled')\"",
                "tier": "fast",
                "emit_output": ANY,
            },
        )

    def test_watch_emits_status_diff_and_new_ready_tasks(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with self.runner.isolated_filesystem(temp_dir=tmpdir):
                self.runner.invoke(main, ["init", "test"], catch_exceptions=False)
                outputs: list[str] = []
                mtimes = iter([0.0, 1.0])
                sleep_calls = 0

                def fake_sleep(_seconds: float) -> None:
                    nonlocal sleep_calls
                    sleep_calls += 1
                    if sleep_calls == 1:
                        self.runner.invoke(
                            main,
                            ["done", "A", "--result", "done"],
                            catch_exceptions=False,
                        )
                        return
                    raise KeyboardInterrupt

                watch_plan(
                    Path("plan.mermaid"),
                    tier="fast",
                    emit_output=outputs.append,
                    sleep_fn=fake_sleep,
                    get_mtime=lambda _path: next(mtimes),
                )

        self.assertGreaterEqual(len(outputs), 4)
        status_payload = json.loads(outputs[0])
        self.assertEqual(status_payload["done"], 1)
        self.assertIn("[CHANGE] Node A: waiting → done", outputs)
        self.assertEqual(
            json.loads(outputs[2]),
            [{"id": "C", "label": "Draft ad copy", "tier": "fast", "type": "task"}],
        )
        self.assertEqual(outputs[-1], "[STOP] Watcher exiting.")

    def test_watch_runs_on_ready_command_with_filtered_payload(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with self.runner.isolated_filesystem(temp_dir=tmpdir):
                self.runner.invoke(main, ["init", "test"], catch_exceptions=False)
                outputs: list[str] = []
                mtimes = iter([0.0, 1.0])
                sleep_calls = 0

                def fake_sleep(_seconds: float) -> None:
                    nonlocal sleep_calls
                    sleep_calls += 1
                    if sleep_calls == 1:
                        self.runner.invoke(
                            main,
                            ["done", "A", "--result", "done"],
                            catch_exceptions=False,
                        )
                        return
                    raise KeyboardInterrupt

                def fake_run_command(**kwargs: object) -> subprocess.CompletedProcess[str]:
                    self.assertEqual(kwargs["input"], outputs[2])
                    return subprocess.CompletedProcess(
                        args=kwargs["args"],
                        returncode=0,
                        stdout="handled ready tasks\n",
                        stderr="",
                    )

                watch_plan(
                    Path("plan.mermaid"),
                    on_ready="python -c \"print('handled')\"",
                    tier="fast",
                    emit_output=outputs.append,
                    sleep_fn=fake_sleep,
                    get_mtime=lambda _path: next(mtimes),
                    run_command=lambda command, **kwargs: fake_run_command(args=command, **kwargs),
                )

        self.assertEqual(
            json.loads(outputs[2]),
            [{"id": "C", "label": "Draft ad copy", "tier": "fast", "type": "task"}],
        )
        self.assertIn("handled ready tasks", outputs)

    def test_watch_emits_new_ready_tasks_when_dependencies_change(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with self.runner.isolated_filesystem(temp_dir=tmpdir):
                self.runner.invoke(main, ["init", "test"], catch_exceptions=False)
                outputs: list[str] = []
                mtimes = iter([0.0, 1.0])
                sleep_calls = 0

                def fake_sleep(_seconds: float) -> None:
                    nonlocal sleep_calls
                    sleep_calls += 1
                    if sleep_calls == 1:
                        plan_path = Path("plan.mermaid")
                        plan_text = plan_path.read_text(encoding="utf-8")
                        plan_path.write_text(
                            plan_text.replace(
                                "    A --> C[Draft ad copy ⏳ ⚡fast]\n",
                                "    C[Draft ad copy ⏳ ⚡fast]\n",
                            ),
                            encoding="utf-8",
                        )
                        return
                    raise KeyboardInterrupt

                watch_plan(
                    Path("plan.mermaid"),
                    tier="fast",
                    emit_output=outputs.append,
                    sleep_fn=fake_sleep,
                    get_mtime=lambda _path: next(mtimes),
                )

        self.assertEqual(json.loads(outputs[0])["done"], 0)
        self.assertEqual(
            json.loads(outputs[1]),
            [{"id": "C", "label": "Draft ad copy", "tier": "fast", "type": "task"}],
        )
        self.assertEqual(outputs[-1], "[STOP] Watcher exiting.")


if __name__ == "__main__":
    unittest.main()
