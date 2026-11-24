"""Own the chat and monitoring lifetimes within one local application process."""

import asyncio
from collections.abc import Awaitable, Callable


class ApplicationRuntime:
    def __init__(self, chat: Callable[[], Awaitable[None]],
                 monitor: Callable[[], Awaitable[None]] | None = None) -> None:
        self.chat = chat
        self.monitor = monitor

    async def run(self, stop: asyncio.Event) -> None:
        tasks = [asyncio.ensure_future(self.chat())]
        if self.monitor is not None:
            tasks.append(asyncio.ensure_future(self.monitor()))
        stopping = asyncio.create_task(stop.wait())
        try:
            done, _ = await asyncio.wait([*tasks, stopping], return_when=asyncio.FIRST_COMPLETED)
            for task in tasks:
                if task in done:
                    task.result()
                    if not stop.is_set():
                        raise RuntimeError('An application worker stopped unexpectedly. Restart the application.')
        finally:
            for pending in [*tasks, stopping]:
                pending.cancel()
            await asyncio.gather(*tasks, stopping, return_exceptions=True)
