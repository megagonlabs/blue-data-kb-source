from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)


class ChangeNotifier:
    """Routes database-scoped and global change hooks.

    Global hooks are the library's own (the search indexer) and run first; database
    hooks are the caller's (`KBSource.add_change_hook`). Every hook sees every event
    (#123): a database hook that raises is logged rather than raised — the write it
    reports is already committed, and one consumer's failure must not stop the
    indexer or the hooks after it — and hooks may add or remove hooks while firing.
    A global hook's error is still raised, after every hook has run.
    """

    def __init__(self) -> None:
        self._hooks: dict[str, list[Callable[[dict], None]]] = {}
        self._global_hooks: list[Callable[[dict], None]] = []

    def add_hook(self, database: str, fn: Callable[[dict], None]) -> None:
        self._hooks.setdefault(database, []).append(fn)

    def remove_hook(self, database: str, fn: Callable[[dict], None]) -> None:
        try:
            self._hooks.get(database, []).remove(fn)
        except ValueError:
            pass

    def add_global_hook(self, fn: Callable[[dict], None]) -> None:
        """Register *fn* to be called for every event across all databases."""
        self._global_hooks.append(fn)

    def remove_global_hook(self, fn: Callable[[dict], None]) -> None:
        try:
            self._global_hooks.remove(fn)
        except ValueError:
            pass

    def fire(self, event: dict) -> None:
        internal_error: Exception | None = None
        for hook in list(self._global_hooks):
            try:
                hook(event)
            except Exception as exc:
                internal_error = internal_error or exc
        for hook in list(self._hooks.get(event["database"], [])):
            try:
                hook(event)
            except Exception:
                logger.exception("change hook %r failed on %s event", hook, event.get("type"))
        if internal_error is not None:
            raise internal_error
