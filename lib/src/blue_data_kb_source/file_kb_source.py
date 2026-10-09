from __future__ import annotations

from blue_data.source.graph.file_graph_source import FileGraphSource

from blue_data_kb_source._json_io import write_json_atomic
from blue_data_kb_source.checks import require_database_name
from blue_data_kb_source.kb_source import KBSource


class FileKBSource(KBSource, FileGraphSource):
    """File-backed KBSource — JSON file per database, single-process persistence, no server required."""

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

