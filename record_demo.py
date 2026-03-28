from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

import io

from playwright.async_api import Page, async_playwright

# Fix Windows console encoding for emoji output
if sys.stdout and sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr and sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "recordings"
VIDEO_NAME = "merdag_demo.webm"
PLAN_PATH = PROJECT_ROOT / "plan.mermaid"
DECISIONS_PATH = PROJECT_ROOT / "decisions.md"
PLAN_LOCK_PATH = PROJECT_ROOT / "plan.mermaid.lock"
DECISIONS_LOCK_PATH = PROJECT_ROOT / "decisions.md.lock"

MERDAG_PORT = int(os.environ.get("MERDAG_PORT", "8000"))
RECORDER_PORT = MERDAG_PORT + 1  # split-screen page served here
VIEWER_URL = f"http://127.0.0.1:{MERDAG_PORT}/"
DEMO_PROMPT = "Launch a social media marketing campaign for a new coffee brand"
STARTUP_WAIT_SECONDS = 2.0
POST_COMPLETE_WAIT_SECONDS = 5.0
SCRIPTED_STEP_DELAY_SECONDS = 2.5
VIEWER_TIMEOUT_SECONDS = 30.0
VIDEO_SIZE = {"width": 1280, "height": 720}
VIEWPORT = {"width": 1280, "height": 720}

SCRIPTED_PLAN_TEMPLATE = """graph TD
    %% task: {task}
    A[Research audience insights ⏳ 🏠local] --> B{{🤖 Pick launch channel? 🟡 🤖codex}}
    A --> C[Draft launch copy ⏳ ⚡fast]
    B -->|Social| D[Build social campaign ⏳ ⚡fast]
    B -->|Search| E[Build search campaign ⏳ ⚡fast]
    C --> F{{🧑 Approve creative? 🟡 🧑human}}
    D --> G[Schedule launch posts ⏳ 🤖codex]
    E --> H[Prepare search rollout ⏳ 🤖codex]
    F -->|Yes| I[Publish approved assets ⏳ 🧑human]
    F -->|No| J[Revise copy deck ⏳ ⚡fast]
    J --> F
"""

# Split-screen HTML layout: 60% iframe (viewer) | 40% terminal pane
SPLIT_SCREEN_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>merdag demo</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 100%; height: 100%; overflow: hidden; background: #0d1117; }
  #container {
    display: flex; width: 100%; height: 100%;
  }
  #viewer-pane {
    width: 60%; height: 100%; border: none;
    border-right: 2px solid #30363d;
  }
  #viewer-pane iframe {
    width: 100%; height: 100%; border: none;
  }
  #term-pane {
    width: 40%; height: 100%;
    background: #1e1e1e;
    overflow-y: auto;
    padding: 12px;
  }
  #term-header {
    color: #888; font-family: 'Cascadia Code', 'Consolas', monospace;
    font-size: 11px; margin-bottom: 8px; border-bottom: 1px solid #333;
    padding-bottom: 6px;
  }
  #term {
    color: #0f0; font-family: 'Cascadia Code', 'Consolas', monospace;
    font-size: 12px; line-height: 1.4; white-space: pre-wrap;
    word-wrap: break-word;
  }
  .term-step { color: #58a6ff; }
  .term-info { color: #0f0; }
  .term-warn { color: #d29922; }
  .term-err  { color: #f85149; }
</style>
</head>
<body>
<div id="container">
  <div id="viewer-pane">
    <iframe id="viewer-frame" src="VIEWER_URL_PLACEHOLDER"></iframe>
  </div>
  <div id="term-pane">
    <div id="term-header">$ merdag simulate</div>
    <pre id="term"></pre>
  </div>
</div>
</body>
</html>"""


def recorder_print(message: str) -> None:
    print(f"[recorder] {message}")


def resolve_demo_mode() -> str:
    configured_mode = os.environ.get("MERDAG_DEMO_MODE", "auto").strip().lower()
    if configured_mode not in {"auto", "simulate", "scripted"}:
        raise RuntimeError("MERDAG_DEMO_MODE must be one of: auto, simulate, scripted.")
    if configured_mode == "auto":
        return "simulate" if os.environ.get("WANDB_API_KEY") else "scripted"
    if configured_mode == "simulate" and not os.environ.get("WANDB_API_KEY"):
        raise RuntimeError("WANDB_API_KEY must be set when MERDAG_DEMO_MODE=simulate.")
    return configured_mode


def build_merdag_command(*args: str) -> list[str]:
    return [sys.executable, "-m", "merdag", *args]


def build_process_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    environment.setdefault("MERDAG_PORT", str(MERDAG_PORT))
    return environment


def build_split_screen_html() -> str:
    return SPLIT_SCREEN_HTML.replace("VIEWER_URL_PLACEHOLDER", VIEWER_URL)


def start_recorder_server() -> HTTPServer:
    """Serve the split-screen HTML on RECORDER_PORT so the iframe can load localhost."""
    html_bytes = build_split_screen_html().encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_bytes)))
            self.end_headers()
            self.wfile.write(html_bytes)

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", RECORDER_PORT), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def stash_workspace_state(staging_dir: Path) -> list[tuple[Path, Path]]:
    backups: list[tuple[Path, Path]] = []
    for source_path in (PLAN_PATH, DECISIONS_PATH):
        if source_path.exists():
            backup_path = staging_dir / source_path.name
            shutil.copy2(source_path, backup_path)
            source_path.unlink()
            backups.append((source_path, backup_path))
    for lock_path in (PLAN_LOCK_PATH, DECISIONS_LOCK_PATH):
        lock_path.unlink(missing_ok=True)
    return backups


def restore_workspace_state(backups: list[tuple[Path, Path]]) -> None:
    for generated_path in (PLAN_PATH, DECISIONS_PATH, PLAN_LOCK_PATH, DECISIONS_LOCK_PATH):
        generated_path.unlink(missing_ok=True)
    for destination_path, backup_path in backups:
        shutil.move(str(backup_path), str(destination_path))


def copy_demo_artifacts() -> None:
    artifact_map = {
        PLAN_PATH: OUTPUT_DIR / "merdag_demo_plan.mermaid",
        DECISIONS_PATH: OUTPUT_DIR / "merdag_demo_decisions.md",
    }
    for source_path, target_path in artifact_map.items():
        if source_path.exists():
            shutil.copy2(source_path, target_path)


def write_scripted_demo_files() -> None:
    PLAN_PATH.write_text(SCRIPTED_PLAN_TEMPLATE.format(task=DEMO_PROMPT), encoding="utf-8")
    DECISIONS_PATH.write_text(f"# Decisions for: {DEMO_PROMPT}\n", encoding="utf-8")


async def wait_for_viewer_in_iframe(page: Page, timeout_seconds: float) -> None:
    """Wait for the merdag viewer SVG to render inside the iframe."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        try:
            frame = page.frame(url=f"*127.0.0.1:{MERDAG_PORT}*")
            if frame is not None:
                await frame.wait_for_selector("#diagram-host svg", timeout=2_000)
                return
        except Exception:
            pass
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(f"Viewer SVG not rendered in iframe after {timeout_seconds}s.")
        await asyncio.sleep(1.0)


async def wait_for_viewer_shell_in_iframe(page: Page, timeout_seconds: float) -> None:
    """Wait for the viewer iframe to load the diagram-host element."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    attempt = 0
    while True:
        try:
            frames = page.frames
            frame = None
            for f in frames:
                if f"127.0.0.1:{MERDAG_PORT}" in f.url:
                    frame = f
                    break
            if frame is not None:
                await frame.wait_for_selector("#diagram-host", timeout=3_000)
                return
            else:
                attempt += 1
                if attempt % 5 == 0:
                    recorder_print(f"Iframe not found yet (attempt {attempt}), reloading page...")
                    await page.reload(wait_until="domcontentloaded")
        except Exception:
            pass
        if asyncio.get_running_loop().time() >= deadline:
            frame_urls = [f.url for f in page.frames]
            raise TimeoutError(
                f"Viewer iframe not ready after {timeout_seconds}s. "
                f"Frames found: {frame_urls}"
            )
        await asyncio.sleep(1.0)


def classify_line(text: str) -> str:
    """Return a CSS class for terminal line coloring."""
    if text.startswith("---") or text.startswith("=== "):
        return "term-step"
    if "Error" in text or "error" in text or "FAIL" in text:
        return "term-err"
    if "WARNING" in text or "Warning" in text:
        return "term-warn"
    return "term-info"


async def append_terminal_line(page: Page, text: str) -> None:
    """Append a line to the terminal pane and auto-scroll."""
    css_class = classify_line(text)
    # Use page.evaluate with argument passing to avoid escaping issues
    await page.evaluate("""([text, cssClass]) => {
        const term = document.getElementById('term');
        if (term) {
            const span = document.createElement('span');
            span.className = cssClass;
            span.textContent = text + '\\n';
            term.appendChild(span);
            const pane = document.getElementById('term-pane');
            if (pane) pane.scrollTop = pane.scrollHeight;
        }
    }""", [text, css_class])


async def reload_viewer_iframe(page: Page) -> None:
    """Reload just the iframe src to pick up plan.mermaid changes."""
    await page.evaluate(f"""() => {{
        const iframe = document.getElementById('viewer-frame');
        if (iframe) iframe.src = '{VIEWER_URL}';
    }}""")


async def stream_to_terminal(
    stream: asyncio.StreamReader | None,
    page: Page,
    prefix: str,
) -> None:
    """Read lines from a subprocess stream and pipe them to the terminal pane."""
    if stream is None:
        return
    while True:
        line = await stream.readline()
        if not line:
            return
        text = line.decode("utf-8", errors="replace").rstrip()
        if text:
            print(f"[{prefix}] {text}")
            await append_terminal_line(page, text)


async def run_short_command(
    args: list[str],
    *,
    process_environment: dict[str, str],
    creationflags: int,
    prefix: str,
    page: Page,
) -> None:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(PROJECT_ROOT),
        env=process_environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        creationflags=creationflags,
    )
    output_lines: list[str] = []
    if process.stdout is not None:
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                output_lines.append(text)
                print(f"[{prefix}] {text}")
                await append_terminal_line(page, text)

    return_code = await process.wait()
    if return_code != 0:
        details = "\n".join(output_lines)
        raise RuntimeError(f"Command failed (exit {return_code}): {' '.join(args)}\n{details}")


async def run_scripted_demo(
    *, process_environment: dict[str, str], creationflags: int, page: Page
) -> int:
    recorder_print("WANDB_API_KEY not set. Running deterministic scripted demo flow.")
    await append_terminal_line(page, "# Scripted demo (no API key)")
    await append_terminal_line(page, "")

    scripted_steps = [
        (build_merdag_command("done", "A", "--result", "Audience research complete."), "done A"),
        (build_merdag_command("done", "C", "--result", "Launch copy drafted."), "done C"),
        (
            build_merdag_command("decide", "B", "--choice", "Social", "--reason",
                                "Short-form channels fit the product launch best."),
            "decide B",
        ),
        (
            build_merdag_command("decide", "F", "--choice", "Yes", "--reason",
                                "The creative is ready for launch."),
            "decide F",
        ),
        (build_merdag_command("done", "D", "--result", "Social campaign assembled."), "done D"),
        (build_merdag_command("done", "G", "--result", "Launch posts scheduled."), "done G"),
        (build_merdag_command("done", "I", "--result", "Approved assets published."), "done I"),
    ]

    for command, label in scripted_steps:
        await asyncio.sleep(SCRIPTED_STEP_DELAY_SECONDS)
        await append_terminal_line(page, f"$ merdag {label}")
        await run_short_command(
            command,
            process_environment=process_environment,
            creationflags=creationflags,
            prefix="scripted",
            page=page,
        )
        await reload_viewer_iframe(page)

    return 0


async def terminate_process(process: asyncio.subprocess.Process | None, name: str) -> None:
    if process is None or process.returncode is not None:
        return
    recorder_print(f"Stopping {name} process.")
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=10.0)
    except asyncio.TimeoutError:
        recorder_print(f"Force killing {name} process.")
        process.kill()
        await process.wait()


async def main() -> None:
    demo_mode = resolve_demo_mode()
    OUTPUT_DIR.mkdir(exist_ok=True)
    final_video_path = OUTPUT_DIR / VIDEO_NAME
    final_video_path.unlink(missing_ok=True)

    process_environment = build_process_environment()
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    with tempfile.TemporaryDirectory() as workspace_backup_dir, \
         tempfile.TemporaryDirectory() as video_dir:

        backups = stash_workspace_state(Path(workspace_backup_dir))
        viewer_process: asyncio.subprocess.Process | None = None
        simulation_process: asyncio.subprocess.Process | None = None
        simulation_stream_task: asyncio.Task[None] | None = None
        recorder_server: HTTPServer | None = None
        browser = None
        context = None
        page: Page | None = None
        recorded_video_path: Path | None = None

        try:
            # Start the merdag serve viewer
            recorder_print("Starting viewer.")
            viewer_process = await asyncio.create_subprocess_exec(
                *build_merdag_command("serve"),
                cwd=str(PROJECT_ROOT),
                env=process_environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=creationflags,
            )

            # Wait for the viewer to start accepting connections
            recorder_print("Waiting for viewer to start...")
            await asyncio.sleep(3.0)

            # Start local server for split-screen page
            recorder_server = start_recorder_server()
            recorder_print(f"Split-screen page on port {RECORDER_PORT}.")

            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context(
                    record_video_dir=video_dir,
                    record_video_size=VIDEO_SIZE,
                    viewport=VIEWPORT,
                )
                page = await context.new_page()

                # Navigate to locally-served split-screen page
                recorder_print("Loading split-screen layout.")
                await page.goto(
                    f"http://127.0.0.1:{RECORDER_PORT}/",
                    wait_until="domcontentloaded",
                )

                # Wait for the viewer iframe to become ready
                recorder_print("Waiting for viewer in iframe.")
                await wait_for_viewer_shell_in_iframe(page, VIEWER_TIMEOUT_SECONDS)
                recorder_print("Viewer ready. Holding intro frame.")
                await asyncio.sleep(STARTUP_WAIT_SECONDS)

                if demo_mode == "simulate":
                    recorder_print("Starting merdag simulate.")
                    await append_terminal_line(page, f"$ merdag simulate \"{DEMO_PROMPT}\"")
                    await append_terminal_line(page, "")

                    simulation_process = await asyncio.create_subprocess_exec(
                        *build_merdag_command("simulate", DEMO_PROMPT),
                        cwd=str(PROJECT_ROOT),
                        env=process_environment,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                        creationflags=creationflags,
                    )

                    # Stream simulation output to terminal pane in real-time
                    simulation_stream_task = asyncio.create_task(
                        stream_to_terminal(simulation_process.stdout, page, "sim")
                    )

                    # Poll: reload iframe periodically to pick up plan changes
                    while simulation_process.returncode is None:
                        await asyncio.sleep(2.0)
                        await reload_viewer_iframe(page)
                        # Check if process finished
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(simulation_process.wait()), timeout=0.1
                            )
                        except asyncio.TimeoutError:
                            pass

                    # Wait for stream to finish (with timeout in case stdout hangs)
                    try:
                        await asyncio.wait_for(simulation_stream_task, timeout=15.0)
                    except asyncio.TimeoutError:
                        recorder_print("Stream task timed out, continuing.")
                        simulation_stream_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await simulation_stream_task
                    simulation_stream_task = None
                    return_code = simulation_process.returncode
                    recorder_print(f"Simulation exited with code {return_code}.")

                    # Final iframe reload to show completed state
                    await reload_viewer_iframe(page)

                else:
                    recorder_print("Starting scripted demo steps.")
                    write_scripted_demo_files()
                    await reload_viewer_iframe(page)
                    await asyncio.sleep(2.0)
                    await wait_for_viewer_in_iframe(page, VIEWER_TIMEOUT_SECONDS)
                    return_code = await run_scripted_demo(
                        process_environment=process_environment,
                        creationflags=creationflags,
                        page=page,
                    )
                    recorder_print("Scripted demo completed.")
                    await reload_viewer_iframe(page)

                if return_code != 0:
                    recorder_print(f"Warning: demo exited with code {return_code} (video still saved).")

                recorder_print(f"Holding final frame for {POST_COMPLETE_WAIT_SECONDS:.0f}s.")
                await asyncio.sleep(POST_COMPLETE_WAIT_SECONDS)

                # Capture video path BEFORE closing context
                if page.video is not None:
                    try:
                        recorded_video_path = Path(await page.video.path())
                        recorder_print(f"Video temp path: {recorded_video_path}")
                    except Exception as exc:
                        recorder_print(f"Warning: could not get video path: {exc}")

                # Close context to flush the video file
                try:
                    await context.close()
                except Exception as exc:
                    recorder_print(f"Warning: context.close() error: {exc}")
                try:
                    await browser.close()
                except Exception as exc:
                    recorder_print(f"Warning: browser.close() error: {exc}")
                browser = None
                context = None

            copy_demo_artifacts()

        finally:
            if context is not None:
                # Try to get video path before closing
                if page is not None and page.video is not None and recorded_video_path is None:
                    with contextlib.suppress(Exception):
                        recorded_video_path = Path(await page.video.path())
                        recorder_print(f"Video temp path (finally): {recorded_video_path}")
                with contextlib.suppress(Exception):
                    await context.close()
            if browser is not None:
                with contextlib.suppress(Exception):
                    await browser.close()

            await terminate_process(simulation_process, "simulation")
            await terminate_process(viewer_process, "viewer")

            if recorder_server is not None:
                recorder_server.shutdown()

            if simulation_stream_task is not None:
                simulation_stream_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await simulation_stream_task

            restore_workspace_state(backups)

        # Fallback: search video_dir for any .webm file
        if recorded_video_path is None or not recorded_video_path.exists():
            video_dir_path = Path(video_dir)
            candidates = list(video_dir_path.glob("*.webm"))
            if candidates:
                recorded_video_path = candidates[0]
                recorder_print(f"Found video via directory scan: {recorded_video_path}")

        if recorded_video_path is None or not recorded_video_path.exists():
            raise RuntimeError("Playwright did not produce a video file.")

        shutil.move(str(recorded_video_path), str(final_video_path))
        recorder_print(f"Saved video to {final_video_path}")


if __name__ == "__main__":
    asyncio.run(main())
