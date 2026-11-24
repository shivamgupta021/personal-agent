"""Keep blocking work inside the caller's trace/deletion lifetime on cancellation."""
import asyncio
from collections.abc import Awaitable


async def drain_on_cancel[T](work: Awaitable[T]) -> T:
    task = asyncio.ensure_future(work)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        # Retrieve a possible worker exception without replacing cancellation.
        if not task.cancelled():
            task.exception()
        raise
