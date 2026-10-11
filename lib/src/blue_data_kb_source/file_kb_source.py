from __future__ import annotations

import os

from blue_data.source.graph.file_graph_source import FileGraphSource

from blue_data_kb_source._file_lock import FileLocks
from blue_data_kb_source._json_io import write_json_atomic
from blue_data_kb_source.checks import require_database_name
from blue_data_kb_source.kb_source import KBSource


class FileKBSource(KBSource, FileGraphSource):
    """File-backed KBSource — JSON file per database, no server required.

    Several processes can share a directory (the CLI and the MCP server both
    default to ~/.blue-kb): every write holds an exclusive lock on the database
    from loading the file to saving it, so one process's change is never
    overwritten by another's stale copy, and saves are atomic, so a reader never
    sees a half-written file (#118, #127). The lock covers one graph primitive,
    not a whole KB operation: two processes adding the same new entity at the
    same moment can each still create it, as two concurrent extraction chunks can.
    """

    def _initialize_connection_properties(self) -> None:
        super()._initialize_connection_properties()
        self.properties["connection"]["protocol"] = "kb/file"

    # Every file this backend reads or writes is named after the database, so the
    # name is checked where the path is built — covering the CLI, the library and
    # the MCP server alike (#116).
    def _db_path(self, database: str) -> str:
        return super()._db_path(require_database_name(database))

    def _txn_path(self, database: str, transaction_id: str) -> str:
        return super()._txn_path(require_database_name(database), transaction_id)

    # FileGraphSource writes by truncating the target and dumping into it, so a
    # failure mid-dump left a half-written file (#118). Same data, written atomically.
    def _save(self, database: str) -> None:
        write_json_atomic(
            self._db_path(database),
            {"nodes": self._nodes[database], "edges": self._edges[database]},
        )

    def _persist_transaction(self, transaction_id: str) -> None:
        txn = self._transactions[transaction_id]
        write_json_atomic(self._txn_path(txn.database, transaction_id), txn.to_dict())

    # ---------- cross-process write lock (#127) ----------
    #
    # FileGraphSource loads the whole database, changes it in memory and saves it
    # back on every write. Without a lock two processes interleave those steps and
    # the later save drops the earlier one's change. Reads need no lock: the file is
    # always replaced whole, so a reader sees either the old version or the new one.

    def _lock_path(self, database: str) -> str:
        # Not "*.json": fetch_databases lists those as databases.
        return os.path.join(self._directory, f".{require_database_name(database)}.lock")

    def _write_lock(self, database: str):
        if not hasattr(self, "_file_locks"):
            self._file_locks = FileLocks()
        return self._file_locks.hold(self._lock_path(database))

    def add_node(self, database, label, properties=None, node_id=None, transaction_id=None) -> str:
        with self._write_lock(database):
            return super().add_node(database, label, properties=properties, node_id=node_id,
                                    transaction_id=transaction_id)

    def delete_node(self, database, node_id, transaction_id=None) -> None:
        with self._write_lock(database):
            super().delete_node(database, node_id, transaction_id=transaction_id)

    def update_node(self, database, node_id, properties, transaction_id=None) -> None:
        with self._write_lock(database):
            super().update_node(database, node_id, properties, transaction_id=transaction_id)

    def add_edge(self, database, source_id, edge_type, target_id, properties=None, transaction_id=None) -> str:
        with self._write_lock(database):
            return super().add_edge(database, source_id, edge_type, target_id, properties=properties,
                                    transaction_id=transaction_id)

    def delete_edge(self, database, source_id, edge_type, target_id, transaction_id=None) -> None:
        with self._write_lock(database):
            super().delete_edge(database, source_id, edge_type, target_id, transaction_id=transaction_id)

    def update_edge(self, database, source_id, edge_type, target_id, properties, transaction_id=None) -> None:
        with self._write_lock(database):
            super().update_edge(database, source_id, edge_type, target_id, properties,
                                transaction_id=transaction_id)

    def commit_transaction(self, transaction_id: str) -> None:
        self._hydrate_transaction(transaction_id)
        with self._write_lock(self._transactions[transaction_id].database):
            super().commit_transaction(transaction_id)

    def create_database(self, database, properties=None, overwrite=False) -> dict:
        with self._write_lock(database):
            return super().create_database(database, properties=properties, overwrite=overwrite)
