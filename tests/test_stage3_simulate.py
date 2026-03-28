from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from merdag.cli import main
from merdag.decisions import pending_decisions
from merdag.simulate import PLAN_GENERATION_SYSTEM_PROMPT, run_simulation


def fake_llm(tier: str, system_prompt: str, user_prompt: str) -> dict[str, int | str]:
    model = "meta-llama/Llama-4-Scout-17B-16E-Instruct" if tier in {"codex", "human"} else "meta-llama/Llama-4-Scout-17B-16E-Instruct-mini"

    if system_prompt == PLAN_GENERATION_SYSTEM_PROMPT:
        return {
            "response": (
                "graph TD\n"
                "A[Research audience ⏳ 🏠local] --> B{🤖 Pick channel? 🟡}\n"
                "A --> C[Draft copy ⏳ ⚡fast]\n"
                "B -->|Social| D[Build social plan ⏳ ⚡fast]\n"
                "B -->|Search| E[Build search plan ⏳ ⚡fast]\n"
                "C --> F{🧑 Approve creative? 🟡}\n"
                "D --> G[Shape launch strategy ⏳ 🤖codex]\n"
                "F -->|Yes| H[Publish assets ⏳ 🧑human]\n"
                "F -->|No| I[Revise assets ⏳ ⚡fast]\n"
                "I --> F\n"
                "G --> H\n"
            ),
            "model": model,
            "tokens_in": 11,
            "tokens_out": 101,
        }

    if "Task: Research audience." in user_prompt:
        return {"response": "Interviewed the target audience and summarized the strongest needs.", "model": model, "tokens_in": 7, "tokens_out": 13}
    if "Task: Draft copy." in user_prompt:
        return {"response": "Drafted launch copy with three tagline options and a supporting CTA.", "model": model, "tokens_in": 8, "tokens_out": 14}
    if "Decision: Pick channel?" in user_prompt:
        return {
            "response": '{"choice": "Social", "reason": "Social gives the coffee brand better visual storytelling reach."}',
            "model": model,
            "tokens_in": 9,
            "tokens_out": 15,
        }
    if "Task: Build social plan." in user_prompt:
        return {"response": "Created a social rollout plan with channel mix, cadence, and asset owners.", "model": model, "tokens_in": 8, "tokens_out": 14}
    if "Task: Shape launch strategy." in user_prompt:
        return {"response": "Produced a detailed launch strategy that aligns content, paid spend, and timing.", "model": model, "tokens_in": 12, "tokens_out": 18}
    if "Decision: Approve creative?" in user_prompt:
        return {
            "response": '```json\n{"choice": "Yes", "reason": "The creative is aligned with the strategy and ready to launch."}\n```',
            "model": model,
            "tokens_in": 10,
            "tokens_out": 16,
        }
    if "Task: Publish assets." in user_prompt:
        return {"response": "Recorded the final publication handoff and confirmed the launch package is ready.", "model": model, "tokens_in": 9, "tokens_out": 12}

    raise AssertionError(f"Unexpected prompt: {user_prompt}")


class Stage3SimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_cli_help_lists_simulate(self) -> None:
        result = self.runner.invoke(main, ["--help"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("simulate", result.output)

    def test_simulate_command_prints_summary_json(self) -> None:
        summary = {"status": "complete", "total_steps": 3}
        with patch("merdag.cli.run_simulation", return_value=summary) as run_mock:
            result = self.runner.invoke(main, ["simulate", "Launch marketing campaign"], catch_exceptions=False)

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(json.loads(result.output.strip()), summary)
        run_mock.assert_called_once()

    def test_run_simulation_completes_generated_plan(self) -> None:
        outputs: list[str] = []
        with TemporaryDirectory() as tmpdir:
            summary = run_simulation(
                "Launch a social media marketing campaign for a new coffee brand",
                plan_path=Path(tmpdir) / "plan.mermaid",
                decisions_path=Path(tmpdir) / "decisions.md",
                emit_output=outputs.append,
                llm_callable=fake_llm,
                sleep_fn=lambda _seconds: None,
            )

            plan_text = (Path(tmpdir) / "plan.mermaid").read_text(encoding="utf-8")
            pending = pending_decisions(Path(tmpdir) / "decisions.md")

        self.assertEqual(summary["status"], "complete")
        self.assertEqual(summary["total_steps"], 7)
        self.assertEqual(summary["models_used"], {"meta-llama/Llama-4-Scout-17B-16E-Instruct": 5, "meta-llama/Llama-4-Scout-17B-16E-Instruct-mini": 3})
        self.assertEqual(summary["nodes_completed"], 5)
        self.assertEqual(summary["nodes_failed"], 2)
        self.assertEqual(summary["decisions_made"], 2)
        self.assertEqual(len(outputs), 7)
        self.assertIn("--- Step 1: complete task ---", outputs[0])
        self.assertIn("--- Step 7: complete task ---", outputs[-1])
        self.assertIn("A[Research audience ✅ 🏠local]", plan_text)
        self.assertIn("B{🤖 Pick channel? ✅}", plan_text)
        self.assertIn("E[Build search plan ❌ ⚡fast]", plan_text)
        self.assertIn("F{🧑 Approve creative? ✅}", plan_text)
        self.assertIn("H[Publish assets ✅ 🧑human]", plan_text)
        self.assertIn("I[Revise assets ❌ ⚡fast]", plan_text)
        self.assertEqual(pending, [])


if __name__ == "__main__":
    unittest.main()
