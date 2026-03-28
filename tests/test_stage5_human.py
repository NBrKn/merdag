from __future__ import annotations

from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen
import unittest
from unittest.mock import ANY, patch

from click.testing import CliRunner

from merdag.cli import main
from merdag.serve import build_viewer_html, create_handler, resolve_port


class Stage5HumanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_cli_help_lists_serve(self) -> None:
        result = self.runner.invoke(main, ["--help"], catch_exceptions=False)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("serve", result.output)

    def test_serve_command_wires_cli_to_server(self) -> None:
        with self.runner.isolated_filesystem():
            with patch("merdag.cli.serve_viewer") as serve_mock:
                result = self.runner.invoke(main, ["serve"], catch_exceptions=False)

        self.assertEqual(result.exit_code, 0)
        serve_mock.assert_called_once_with(
            plan_path=Path("plan.mermaid"),
            decisions_path=Path("decisions.md"),
            emit_output=ANY,
        )

    def test_status_human_prints_pretty_summary(self) -> None:
        with TemporaryDirectory() as tmpdir:
            with self.runner.isolated_filesystem(temp_dir=tmpdir):
                self.runner.invoke(main, ["init", "Launch marketing campaign"], catch_exceptions=False)
                self.runner.invoke(main, ["done", "A", "--result", "done"], catch_exceptions=False)

                result = self.runner.invoke(main, ["status", "--human"], catch_exceptions=False)

        self.assertEqual(result.exit_code, 0)
        self.assertIn("📊 merdag status: Launch marketing campaign", result.output)
        self.assertIn("✅ Done:              1/9", result.output)
        self.assertIn("🟡 Decisions Pending: 2", result.output)
        self.assertIn("A  ✅ Research competitors", result.output)
        self.assertIn("B  🟡 Pick channel?", result.output)
        self.assertIn("🤖codex", result.output)

    def test_viewer_html_includes_live_mermaid_refresh_requirements(self) -> None:
        html = build_viewer_html()
        self.assertIn("https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js", html)
        self.assertIn("grid-template-columns: 7fr 3fr", html)
        self.assertIn("merdag — Live Plan Viewer", html)
        self.assertIn("setInterval(refresh, 2000)", html)
        self.assertIn("mermaid.render(renderId, newContent)", html)
        self.assertIn("lastPlanText = null;", html)
        self.assertIn("lastDecisionsText = null;", html)
        self.assertNotIn("mermaid.init()", html)

    def test_server_endpoints_return_html_and_raw_files(self) -> None:
        with TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / "plan.mermaid"
            decisions_path = Path(tmpdir) / "decisions.md"
            plan_text = "graph TD\nA[Test ✅ 🏠local]\n"
            decisions_text = "# Decisions\n\n## Decision: Demo (node B)\n"
            plan_path.write_text(plan_text, encoding="utf-8")
            decisions_path.write_text(decisions_text, encoding="utf-8")

            server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(plan_path, decisions_path))
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()

            try:
                port = server.server_address[1]
                with urlopen(f"http://127.0.0.1:{port}/") as response:
                    html = response.read().decode("utf-8")
                    self.assertEqual(response.status, 200)
                    self.assertIn("merdag — Live Plan Viewer", html)

                with urlopen(f"http://127.0.0.1:{port}/plan") as response:
                    self.assertEqual(response.read().decode("utf-8"), plan_text)

                with urlopen(f"http://127.0.0.1:{port}/decisions") as response:
                    self.assertEqual(response.read().decode("utf-8"), decisions_text)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_server_returns_not_found_for_missing_files(self) -> None:
        with TemporaryDirectory() as tmpdir:
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                create_handler(Path(tmpdir) / "missing-plan.mermaid", Path(tmpdir) / "missing-decisions.md"),
            )
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()

            try:
                port = server.server_address[1]
                with self.assertRaises(HTTPError) as plan_error:
                    urlopen(f"http://127.0.0.1:{port}/plan")
                self.assertEqual(plan_error.exception.code, 404)

                with self.assertRaises(HTTPError) as decisions_error:
                    urlopen(f"http://127.0.0.1:{port}/decisions")
                self.assertEqual(decisions_error.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_resolve_port_uses_env_override_and_validates_range(self) -> None:
        self.assertEqual(resolve_port({"MERDAG_PORT": "8123"}), 8123)

        with self.assertRaisesRegex(ValueError, "integer"):
            resolve_port({"MERDAG_PORT": "abc"})

        with self.assertRaisesRegex(ValueError, "between 1 and 65535"):
            resolve_port({"MERDAG_PORT": "70000"})


if __name__ == "__main__":
    unittest.main()
