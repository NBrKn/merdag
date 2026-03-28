from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from click.testing import CliRunner

from merdag.cli import main
from merdag.decisions import pending_decisions
from merdag.parser import parse_plan, parse_plan_text


class Stage2CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_help_command_works(self) -> None:
        result = self.runner.invoke(main, ["--help"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("init", result.output)
        self.assertIn("decide", result.output)

    def test_parser_handles_nodes_and_edges_on_same_line(self) -> None:
        raw = (
            "graph TD\n"
            "A[Research competitors ✅ 🏠local] --> B{🤖 Pick channel? 🟡 🤖codex}\n"
            "B -->|Social| C[Create social campaign ⏳ ⚡fast]\n"
        )

        plan = parse_plan_text(raw)
        self.assertEqual(plan.nodes["A"].label, "Research competitors")
        self.assertEqual(plan.nodes["B"].decision_type, "🤖")
        self.assertEqual(plan.edges[0].source, "A")
        self.assertEqual(plan.edges[1].label, "Social")

    def test_parser_handles_multiple_edges_on_one_line(self) -> None:
        raw = (
            "graph TD\n"
            "A[Research competitors ✅ 🏠local] --> B[Draft ad copy ⏳ ⚡fast] -->|Social| C[Create social campaign ⏳ ⚡fast]\n"
        )

        plan = parse_plan_text(raw)
        self.assertEqual([(edge.source, edge.target, edge.label) for edge in plan.edges], [
            ("B", "C", "Social"),
            ("A", "B", None),
        ])

    def test_stage2_done_when_flow(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with self.runner.isolated_filesystem(temp_dir=tmpdir):
                init_result = self.runner.invoke(main, ["init", "Launch marketing campaign"], catch_exceptions=False)
                self.assertEqual(init_result.exit_code, 0)

                status_payload = json.loads(self.runner.invoke(main, ["status"], catch_exceptions=False).output)
                self.assertGreaterEqual(status_payload["total"], 8)

                all_ready = json.loads(self.runner.invoke(main, ["next"], catch_exceptions=False).output)
                self.assertEqual([item["id"] for item in all_ready], ["A"])

                fast_ready = json.loads(self.runner.invoke(main, ["next", "--tier", "fast"], catch_exceptions=False).output)
                self.assertEqual(fast_ready, [])

                done_payload = json.loads(
                    self.runner.invoke(
                        main,
                        ["done", "A", "--result", "Found 5 competitors"],
                        catch_exceptions=False,
                    ).output
                )
                self.assertEqual(done_payload["status"], "done")

                status_after_done = json.loads(self.runner.invoke(main, ["status"], catch_exceptions=False).output)
                done_nodes = {node["id"] for node in status_after_done["nodes"] if node["status"] == "done"}
                self.assertIn("A", done_nodes)

                unlocked = json.loads(self.runner.invoke(main, ["next"], catch_exceptions=False).output)
                self.assertEqual([item["id"] for item in unlocked], ["B", "C"])

                decide_result = self.runner.invoke(
                    main,
                    ["decide", "B", "--choice", "Social", "--reason", "Higher engagement"],
                    catch_exceptions=False,
                )
                self.assertEqual(decide_result.exit_code, 0)
                self.assertEqual(json.loads(self.runner.invoke(main, ["decisions"], catch_exceptions=False).output), [])

    def test_cli_flow_unlocks_and_skips_expected_nodes(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with self.runner.isolated_filesystem(temp_dir=tmpdir):
                init_result = self.runner.invoke(main, ["init", "Launch marketing campaign"], catch_exceptions=False)
                self.assertEqual(init_result.exit_code, 0)
                self.assertTrue(Path("plan.mermaid").exists())
                self.assertTrue(Path("decisions.md").exists())

                status_result = self.runner.invoke(main, ["status"], catch_exceptions=False)
                status_payload = json.loads(status_result.output)
                self.assertGreaterEqual(status_payload["total"], 8)

                next_result = self.runner.invoke(main, ["next"], catch_exceptions=False)
                next_payload = json.loads(next_result.output)
                self.assertEqual([item["id"] for item in next_payload], ["A"])

                next_fast_result = self.runner.invoke(main, ["next", "--tier", "fast"], catch_exceptions=False)
                self.assertEqual(json.loads(next_fast_result.output), [])

                done_result = self.runner.invoke(
                    main,
                    ["done", "A", "--result", "Found 5 competitors"],
                    catch_exceptions=False,
                )
                self.assertEqual(done_result.exit_code, 0)

                next_after_done = json.loads(self.runner.invoke(main, ["next"], catch_exceptions=False).output)
                self.assertEqual([item["id"] for item in next_after_done], ["B", "C"])

                decide_result = self.runner.invoke(
                    main,
                    ["decide", "B", "--choice", "Social", "--reason", "Higher engagement"],
                    catch_exceptions=False,
                )
                decide_payload = json.loads(decide_result.output)
                self.assertEqual(decide_payload["skipped_nodes"], ["E"])

                plan = parse_plan(Path("plan.mermaid"))
                self.assertEqual(plan.nodes["B"].status, "done")
                self.assertEqual(plan.nodes["E"].status, "failed")
                self.assertEqual(plan.nodes["F"].status, "pending_decision")

                pending = pending_decisions(Path("decisions.md"))
                self.assertEqual([entry.node for entry in pending], [])


if __name__ == "__main__":
    unittest.main()
