"""Standalone durable processing worker entrypoint."""

from __future__ import annotations

import asyncio
import signal

from app.db.session import engine
from app.services.task_worker import ProcessingTaskWorker


async def main() -> None:
    worker = ProcessingTaskWorker()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stopped.set)
        except NotImplementedError:
            pass
    await worker.start()
    try:
        await stopped.wait()
    finally:
        await worker.stop()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
