"""Run a coroutine to completion from synchronous code, inside an event loop or not.

DataSource's methods are synchronous, and the registry and its hosts call them that
way — some from inside a running event loop (an async web server, an MCP host). The
KB's own operations are async, so its sync entry points need a bridge, and
`asyncio.run` refuses to start a second loop in a thread that already has one
(RuntimeError). Inside a running loop the coroutine runs on a worker thread with its
own loop instead, and the caller blocks until it finishes (#147, design/14 §6).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")


def run_sync(coro: Coroutine[Any, Any, T]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
