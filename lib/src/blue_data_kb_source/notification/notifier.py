from __future__ import annotations

from typing import Callable


class ChangeNotifier:
    """Routes database-scoped and global change hooks."""

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
        for hook in self._hooks.get(event["database"], []):
            hook(event)
        for hook in self._global_hooks:
            hook(event)
