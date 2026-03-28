from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from playwright.async_api import Page, async_playwright

PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "recordings"
VIDEO_NAME = "merdag_demo.webm"
PLAN_PATH = PROJECT_ROOT / "plan.mermaid"
DECISIONS_PATH = PROJECT_ROOT / "decisions.md"
PLAN_LOCK_PATH = PROJECT_ROOT / "plan.mermaid.lock"
DECISIONS_LOCK_PATH = PROJECT_ROOT / "decisions.md.lock"

MERDAG_PORT = int(os.environ.get("MERDAG_PORT", "8000"))
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


async def wait_for_viewer_shell(page: Page, timeout_seconds: float) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        try:
            await page.goto(VIEWER_URL, wait_until="domcontentloaded")
            await page.wait_for_selector("#diagram-host", timeout=2_000)
            return
        except Exception:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"Viewer not ready after {timeout_seconds} seconds.")
            await asyncio.sleep(1.0)


async def wait_for_rendered_graph(page: Page, timeout_seconds: float) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        try:
            await page.wait_for_selector("#diagram-host svg", timeout=2_000)
            return
        except Exception:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"Graph did not render after {timeout_seconds} seconds.")
            await asyncio.sleep(1.0)


async def stream_process_output(
    stream: asyncio.StreamReader | None,
    prefix: str,
) -> None:
    if stream is None:
        return

    while True:
        line = await stream.readline()
        if not line:
            return
        text = line.decode("utf-8", errors="replace").rstrip()
        if text:
            print(f"[{prefix}] {text}")


async def run_short_command(
    args: list[str],
    *,
    process_environment: dict[str, str],
    creationflags: int,
    prefix: str,
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

    return_code = await process.wait()
    if return_code != 0:
        details = "\n".join(output_lines)
        raise RuntimeError(f"Command failed with exit code {return_code}: {' '.join(args)}\n{details}")


async def run_scripted_demo(*, process_environment: dict[str, str], creationflags: int) -> int:
    recorder_print("WANDB_API_KEY not set. Running deterministic scripted demo flow.")

    scripted_steps = [
        (
            build_merdag_command("done", "A", "--result", "Audience research complete."),
            "scripted",
        ),
        (
            build_merdag_command("done", "C", "--result", "Launch copy drafted."),
            "scripted",
        ),
        (
            build_merdag_command(
                "decide",
                "B",
                "--choice",
                "Social",
                "--reason",
                "Short-form channels fit the product launch best.",
            ),
            "scripted",
        ),
        (
            build_merdag_command(
                "decide",
                "F",
                "--choice",
                "Yes",
                "--reason",
                "The creative is ready for launch.",
            ),
            "scripted",
        ),
        (
            build_merdag_command("done", "D", "--result", "Social campaign assembled."),
            "scripted",
        ),
        (
            build_merdag_command("done", "G", "--result", "Launch posts scheduled."),
            "scripted",
        ),
        (
            build_merdag_command("done", "I", "--result", "Approved assets published."),
            "scripted",
        ),
    ]

    for command, prefix in scripted_steps:
        await asyncio.sleep(SCRIPTED_STEP_DELAY_SECONDS)
        await run_short_command(
            command,
            process_environment=process_environment,
            creationflags=creationflags,
            prefix=prefix,
        )

    return 0


async def terminate_process(process: asyncio.subprocess.Process | None, name: str) -> None:
    if process is None:
        return
    if process.returncode is not None:
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

    with tempfile.TemporaryDirectory() as workspace_backup_dir, tempfile.TemporaryDirectory() as video_dir:
        backups = stash_workspace_state(Path(workspace_backup_dir))
        viewer_process: asyncio.subprocess.Process | None = None
        simulation_process: asyncio.subprocess.Process | None = None
        viewer_log_task: asyncio.Task[None] | None = None
        simulation_log_task: asyncio.Task[None] | None = None
        browser = None
        context = None
        page = None
        recorded_video_path: Path | None = None

        try:
            recorder_print("Starting viewer.")
            viewer_process = await asyncio.create_subprocess_exec(
                *build_merdag_command("serve"),
                cwd=str(PROJECT_ROOT),
                env=process_environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=creationflags,
            )
            viewer_log_task = asyncio.create_task(stream_process_output(viewer_process.stdout, "viewer"))

            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                context = await browser.new_context(
                    record_video_dir=video_dir,
                    record_video_size=VIDEO_SIZE,
                    viewport=VIEWPORT,
                )
                page = await context.new_page()

                recorder_print("Waiting for viewer page.")
                await wait_for_viewer_shell(page, VIEWER_TIMEOUT_SECONDS)
                recorder_print("Viewer ready. Holding intro frame.")
                await asyncio.sleep(STARTUP_WAIT_SECONDS)

                if demo_mode == "simulate":
                    recorder_print("Starting merdag simulate.")
                    simulation_process = await asyncio.create_subprocess_exec(
                        *build_merdag_command("simulate", DEMO_PROMPT),
                        cwd=str(PROJECT_ROOT),
                        env=process_environment,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                        creationflags=creationflags,
                    )
                    simulation_log_task = asyncio.create_task(
                        stream_process_output(simulation_process.stdout, "sim")
                    )

                    await wait_for_rendered_graph(page, VIEWER_TIMEOUT_SECONDS)
                    return_code = await simulation_process.wait()
                    await simulation_log_task
                    simulation_log_task = None
                    recorder_print(f"Simulation exited with code {return_code}.")
                else:
                    recorder_print("Starting scripted demo steps.")
                    write_scripted_demo_files()
                    await wait_for_rendered_graph(page, VIEWER_TIMEOUT_SECONDS)
                    return_code = await run_scripted_demo(
                        process_environment=process_environment,
                        creationflags=creationflags,
                    )
                    recorder_print("Scripted demo completed.")

                if return_code != 0:
                    raise RuntimeError(f"Simulation exited with code {return_code}.")

                recorder_print(f"Holding final frame for {POST_COMPLETE_WAIT_SECONDS:.0f}s.")
                await asyncio.sleep(POST_COMPLETE_WAIT_SECONDS)

                with contextlib.suppress(Exception):
                    await context.close()
                if page.video is not None:
                    with contextlib.suppress(Exception):
                        recorded_video_path = Path(await page.video.path())
                with contextlib.suppress(Exception):
                    await browser.close()
                browser = None
                context = None

            copy_demo_artifacts()

        finally:
            if context is not None:
                with contextlib.suppress(Exception):
                    await context.close()
                if page is not None and page.video is not None and recorded_video_path is None:
                    with contextlib.suppress(Exception):
                        recorded_video_path = Path(await page.video.path())

            if browser is not None:
                with contextlib.suppress(Exception):
                    await browser.close()

            await terminate_process(simulation_process, "simulation")
            await terminate_process(viewer_process, "viewer")

            if simulation_log_task is not None:
                await simulation_log_task
            if viewer_log_task is not None:
                await viewer_log_task

            restore_workspace_state(backups)

        if recorded_video_path is None or not recorded_video_path.exists():
            raise RuntimeError("Playwright did not produce a video file.")

        shutil.move(str(recorded_video_path), str(final_video_path))
        recorder_print(f"Saved video to {final_video_path}")


if __name__ == "__main__":
    asyncio.run(main())