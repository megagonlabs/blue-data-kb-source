"""Neo4j connection URIs → the connection dict NEO4JKBSource takes.

Importing this does not import the neo4j driver, so the CLI and MCP server can
parse a URI without the `neo4j` extra installed.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

# The schemes the neo4j driver accepts. `+s` verifies the server certificate,
# `+ssc` encrypts but accepts a self-signed one.
NEO4J_SCHEMES = ("bolt", "bolt+s", "bolt+ssc", "neo4j", "neo4j+s", "neo4j+ssc")


def neo4j_uri_connection(uri: str) -> dict[str, Any]:
    """`scheme`, `host` and (if given) `port` from a Neo4j URI such as
    `neo4j+s://kb.example.com:7687`. A URI without a scheme means `bolt`.

    The scheme goes under `scheme`, the key blue-data's NEO4JSource builds the
    driver URI from. It used to go under `protocol`, which the driver never
    reads, so a TLS URI was dialled as plain `bolt://` (#121).

    Raises ValueError for any other scheme, rather than falling back to an
    unencrypted connection.
    """
    parsed = urlparse(uri if "://" in uri else f"bolt://{uri}")
    scheme = parsed.scheme.lower()
    if scheme not in NEO4J_SCHEMES:
        raise ValueError(f"unsupported Neo4j URI scheme '{parsed.scheme}' in {uri!r}; "
                         f"use one of: {', '.join(NEO4J_SCHEMES)}")
    connection: dict[str, Any] = {"scheme": scheme, "host": parsed.hostname or "localhost"}
    if parsed.port:
        connection["port"] = parsed.port
    return connection
