"""Windows-compatible MinerU CLI adapter with streamable progress output."""

import asyncio
import queue
import subprocess
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.core.config import get_settings

ProgressCallback = Callable[[str], Awaitable[None]]


class MinerUError(RuntimeError):
    """Raised when MinerU cannot produce a Markdown document."""


class _MinerUCancelled(RuntimeError):
    """Internal bridge used to stop a thread-owned child process cleanly."""


def _terminate_process(process: subprocess.Popen[str]) -> None:
    """Stop MinerU gracefully, escalating to kill after a short deadline."""

    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _run_mineru_streaming(
    command: list[str],
    output_path: Path,
    timeout_seconds: int,
    event_loop: asyncio.AbstractEventLoop,
    on_output: ProgressCallback | None,
    cancel_event: threading.Event,
) -> bytes:
    """Own the Windows child process in a thread and bridge output to asyncio.

    The Windows event loop used by the development server cannot reliably drive
    every subprocess configuration. A dedicated thread owns ``Popen`` and its
    blocking stdout stream, while ``run_coroutine_threadsafe`` forwards complete
    lines to the async Worker for progress persistence.
    """

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise MinerUError(f"未找到 MinerU 命令“{command[0]}”，请安装 MinerU 4.x 或配置 MINERU_COMMAND") from exc

    # A separate reader prevents a full stdout pipe from blocking MinerU while
    # the owner loop checks timeouts and cancellation.
    lines: queue.Queue[str] = queue.Queue()
    reader_done = threading.Event()

    def read_output() -> None:
        try:
            if process.stdout:
                for line in process.stdout:
                    lines.put(line.rstrip())
        finally:
            reader_done.set()

    reader = threading.Thread(target=read_output, name="mineru-output-reader", daemon=True)
    reader.start()
    # Retain only the tail of output: it is enough for actionable errors without
    # storing an unbounded parser log in memory or returning it through the API.
    captured: list[str] = []
    started_at = time.monotonic()
    try:
        while True:
            if cancel_event.is_set():
                _terminate_process(process)
                raise _MinerUCancelled
            if time.monotonic() - started_at > timeout_seconds:
                _terminate_process(process)
                raise MinerUError("MinerU 转换超时，请稍后重试或提高超时时间")
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                line = ""
            if line:
                captured.append(line)
                del captured[:-40]
                if on_output:
                    # Block this producer until the async callback has consumed
                    # the line, preserving output order and propagating failures.
                    callback = asyncio.run_coroutine_threadsafe(on_output(line), event_loop)
                    callback.result(timeout=10)
            if process.poll() is not None and reader_done.is_set() and lines.empty():
                break
    finally:
        if process.poll() is None:
            _terminate_process(process)
        reader.join(timeout=1)

    if process.returncode != 0:
        detail = "\n".join(captured).strip() or "未知错误"
        raise MinerUError(f"MinerU 转换失败：{detail[-1200:]}")
    if not output_path.is_file():
        raise MinerUError("MinerU 未生成 Markdown 文件")
    content = output_path.read_bytes()
    if not content.strip():
        raise MinerUError("MinerU 生成的 Markdown 内容为空")
    return content


async def convert_pdf_to_markdown(
    pdf_path: Path,
    *,
    on_output: ProgressCallback | None = None,
) -> bytes:
    """Run MinerU off-loop while preserving output, timeout, and cancellation.

    Every conversion receives a temporary output directory, so partial files
    never appear in permanent attachment storage. ``shield`` lets the coroutine
    signal cancellation to the thread-owned process before awaiting its cleanup.
    """

    settings = get_settings()
    with tempfile.TemporaryDirectory(prefix="medical-knowledge-mineru-") as directory:
        output_path = Path(directory) / "document.md"
        command = [
            settings.mineru_command,
            "parse",
            str(pdf_path),
            "-o",
            str(output_path),
            "--tier",
            settings.mineru_tier,
        ]
        cancel_event = threading.Event()
        # asyncio.to_thread keeps FastAPI responsive while MinerU performs CPU,
        # model and filesystem work through a synchronous CLI process.
        thread_task = asyncio.create_task(
            asyncio.to_thread(
                _run_mineru_streaming,
                command,
                output_path,
                settings.mineru_timeout_seconds,
                asyncio.get_running_loop(),
                on_output,
                cancel_event,
            )
        )
        try:
            return await asyncio.shield(thread_task)
        except asyncio.CancelledError:
            cancel_event.set()
            await asyncio.gather(thread_task, return_exceptions=True)
            raise
        except _MinerUCancelled as exc:
            raise asyncio.CancelledError from exc
