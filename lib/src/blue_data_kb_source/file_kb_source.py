from __future__ import annotations

from blue_data.source.graph.file_graph_source import FileGraphSource

from blue_data_kb_source.kb_source import KBSource


class FileKBSource(KBSource, FileGraphSource):
    """File-backed KBSource — JSON file per database, single-process persistence, no server required."""

    def _initialize_connection_properties(self) -> None:
        super()._initialize_connection_properties()
        self.properties["connection"]["protocol"] = "kb/file"
