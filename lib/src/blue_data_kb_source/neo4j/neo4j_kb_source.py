from __future__ import annotations

from blue_data.source.graph.neo4j.neo4j_graph_source import NEO4JSource

from blue_data_kb_source.kb_source import KBSource


class NEO4JKBSource(KBSource, NEO4JSource):
    """Neo4j-backed KBSource — persistent, concurrent, production graph backend.

    Kept out of blue_data_kb_source's top-level module and package __init__ so that
    `import blue_data_kb_source` never requires the `neo4j` driver to be installed —
    only importing this module does. Use DataSource.instantiate() with this class's
    dotted path to construct one from a config-driven backend choice without an
    eager import; see platform/cli/config.py and platform/mcp/config.py.
    """

    def _initialize_connection_properties(self) -> None:
        super()._initialize_connection_properties()
        self.properties["connection"]["protocol"] = "kb/neo4j"
