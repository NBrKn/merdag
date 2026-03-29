from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os

DEFAULT_PORT = 8000

VIEWER_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>merdag — Live Plan Viewer</title>
  <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0f172a;
      --panel: #111827;
      --border: #334155;
      --text: #e5e7eb;
      --muted: #94a3b8;
      --accent: #38bdf8;
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      min-height: 100vh;
      font-family: Arial, Helvetica, sans-serif;
      background: linear-gradient(180deg, #020617 0%, var(--bg) 100%);
      color: var(--text);
    }

    header {
      padding: 20px 24px 12px;
      border-bottom: 1px solid var(--border);
      background: rgba(15, 23, 42, 0.92);
      position: sticky;
      top: 0;
      z-index: 10;
      backdrop-filter: blur(8px);
    }

    h1 {
      margin: 0 0 6px;
      font-size: 28px;
    }

    #last-updated {
      color: var(--muted);
      font-size: 14px;
    }

    main {
      display: grid;
      grid-template-columns: 7fr 3fr;
      gap: 16px;
      padding: 16px 24px 24px;
      min-height: calc(100vh - 90px);
    }

    section {
      background: rgba(15, 23, 42, 0.88);
      border: 1px solid var(--border);
      border-radius: 14px;
      overflow: hidden;
      box-shadow: 0 18px 40px rgba(2, 6, 23, 0.28);
    }

    .panel-title {
      margin: 0;
      padding: 14px 16px;
      border-bottom: 1px solid var(--border);
      color: var(--accent);
      font-size: 16px;
      letter-spacing: 0.02em;
    }

    #diagram-panel {
      padding: 12px;
      overflow: auto;
    }

    #diagram-host {
      min-height: 480px;
      display: flex;
      align-items: flex-start;
      justify-content: center;
      padding: 8px;
    }

    #decisions-panel {
      padding: 0;
    }

    #decisions-content {
      margin: 0;
      padding: 16px;
      min-height: 100%;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      color: var(--text);
      font-family: Consolas, "Courier New", monospace;
      font-size: 14px;
      line-height: 1.5;
    }

    .note {
      color: var(--muted);
    }

    .error {
      color: #fca5a5;
    }

    @media (max-width: 980px) {
      main {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <header>
    <h1>merdag — Live Plan Viewer</h1>
    <div id="last-updated">Last updated: waiting for first refresh...</div>
  </header>
  <main>
    <section>
      <h2 class="panel-title">Diagram</h2>
      <div id="diagram-panel">
        <div id="diagram-host">
          <div class="note">Waiting for plan.mermaid...</div>
        </div>
      </div>
    </section>
    <section id="decisions-panel">
      <h2 class="panel-title">Decisions</h2>
      <div id="decisions-content" class="note">Waiting for decisions.md...</div>
    </section>
  </main>
  <script>
    mermaid.initialize({ startOnLoad: false, theme: 'dark' });

    const diagramHost = document.getElementById('diagram-host');
    const decisionsContent = document.getElementById('decisions-content');
    const lastUpdated = document.getElementById('last-updated');
    let lastPlanText = null;
    let lastDecisionsText = null;
    let diagramCounter = 0;

    function escapeHtml(value) {
      return value
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;');
    }

    function setTimestamp() {
      lastUpdated.textContent = 'Last updated: ' + new Date().toLocaleTimeString();
    }

    async function renderPlan(newContent) {
      diagramCounter += 1;

      const renderId = 'diagram-' + diagramCounter;
      try {
        const renderResult = await mermaid.render(renderId, newContent);
        diagramHost.innerHTML = renderResult.svg;
      } catch (err) {
        // Don't clear the previous good render on parse error
        console.warn('Mermaid parse error:', err);
      }
    }

    async function refresh() {
      try {
        const [planResponse, decisionsResponse] = await Promise.all([
          fetch('/plan'),
          fetch('/decisions'),
        ]);

        const planText = await planResponse.text();
        const decisionsText = await decisionsResponse.text();

        if (planResponse.ok) {
          if (planText !== lastPlanText) {
            await renderPlan(planText);
            lastPlanText = planText;
          }
        } else {
          diagramHost.innerHTML = '<div class="error">' + escapeHtml(planText) + '</div>';
          lastPlanText = null;
        }

        if (decisionsText !== lastDecisionsText || !decisionsResponse.ok) {
          const className = decisionsResponse.ok ? '' : 'error';
          decisionsContent.className = className;
          decisionsContent.innerHTML = '<pre>' + escapeHtml(decisionsText) + '</pre>';
          lastDecisionsText = decisionsText;
        }

        setTimestamp();
      } catch (error) {
        lastPlanText = null;
        lastDecisionsText = null;
        diagramHost.innerHTML = '<div class="error">Viewer refresh failed: ' + escapeHtml(String(error)) + '</div>';
        decisionsContent.className = 'error';
        decisionsContent.innerHTML = '<pre>Viewer refresh failed.</pre>';
      }
    }

    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>
"""


def resolve_port(env: Mapping[str, str] | None = None) -> int:
    values = env if env is not None else os.environ
    raw_port = values.get("MERDAG_PORT", str(DEFAULT_PORT))
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("MERDAG_PORT must be an integer") from exc

    if not 0 < port < 65536:
        raise ValueError("MERDAG_PORT must be between 1 and 65535")
    return port


def read_raw_file(path: str | Path, missing_name: str) -> tuple[HTTPStatus, str]:
    file_path = Path(path)
    if not file_path.exists():
        return HTTPStatus.NOT_FOUND, f"{missing_name} not found\n"
    return HTTPStatus.OK, file_path.read_text(encoding="utf-8")


def build_viewer_html() -> str:
    return VIEWER_HTML


def create_handler(
    plan_path: str | Path = Path("plan.mermaid"),
    decisions_path: str | Path = Path("decisions.md"),
) -> type[BaseHTTPRequestHandler]:
    plan_file = Path(plan_path)
    decisions_file = Path(decisions_path)

    class MerdagRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                self._send_response(HTTPStatus.OK, build_viewer_html(), "text/html; charset=utf-8")
                return

            if self.path == "/plan":
                status, body = read_raw_file(plan_file, "plan.mermaid")
                self._send_response(status, body, "text/plain; charset=utf-8")
                return

            if self.path == "/decisions":
                status, body = read_raw_file(decisions_file, "decisions.md")
                self._send_response(status, body, "text/plain; charset=utf-8")
                return

            self._send_response(HTTPStatus.NOT_FOUND, "Not Found\n", "text/plain; charset=utf-8")

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

        def _send_response(self, status: HTTPStatus, body: str, content_type: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return MerdagRequestHandler


def serve_viewer(
    *,
    plan_path: str | Path = Path("plan.mermaid"),
    decisions_path: str | Path = Path("decisions.md"),
    host: str = "127.0.0.1",
    env: Mapping[str, str] | None = None,
    emit_output=print,
) -> None:
    port = resolve_port(env)
    server = ThreadingHTTPServer((host, port), create_handler(plan_path, decisions_path))
    emit_output(f"Serving merdag viewer at http://localhost:{port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
