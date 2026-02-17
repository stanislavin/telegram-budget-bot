"""Per-chat asyncio queue ensuring expenses are processed sequentially."""

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

# One queue per chat_id
_chat_queues: dict[str, asyncio.Queue] = {}
_chat_workers: dict[str, asyncio.Task] = {}


async def _chat_worker(chat_id: str):
    """Process queued coroutines for a single chat, one at a time."""
    queue = _chat_queues[chat_id]
    while True:
        coro = await queue.get()
        try:
            await coro
        except Exception:
            logger.exception("Error processing queued expense for chat %s", chat_id)
        finally:
            queue.task_done()


def _get_or_create_queue(chat_id: str) -> asyncio.Queue:
    """Return the existing queue for *chat_id* or create one with a worker."""
    if chat_id not in _chat_queues:
        _chat_queues[chat_id] = asyncio.Queue()
        _chat_workers[chat_id] = asyncio.create_task(
            _chat_worker(chat_id)
        )
    return _chat_queues[chat_id]


async def enqueue_expense(chat_id: str, coro: Awaitable) -> None:
    """Add an awaitable to the per-chat processing queue."""
    queue = _get_or_create_queue(chat_id)
    await queue.put(coro)


def queue_size(chat_id: str) -> int:
    """Return the current number of items waiting in the chat's queue."""
    queue = _chat_queues.get(chat_id)
    if queue is None:
        return 0
    return queue.qsize()
