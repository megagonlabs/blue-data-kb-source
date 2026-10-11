"""Neo4j URIs keep their scheme, so a TLS URI is dialled over TLS (#121)."""
import pytest

from blue_data_kb_source.neo4j.uri import NEO4J_SCHEMES, neo4j_uri_connection


@pytest.mark.parametrize("scheme", NEO4J_SCHEMES)
def test_every_driver_scheme_is_kept(scheme):
    assert neo4j_uri_connection(f"{scheme}://kb.example.com:7688") == {
        "scheme": scheme, "host": "kb.example.com", "port": 7688,
    }


def test_scheme_is_case_insensitive_and_port_optional():
    assert neo4j_uri_connection("NEO4J+S://kb.example.com") == {"scheme": "neo4j+s", "host": "kb.example.com"}


def test_uri_without_a_scheme_means_bolt():
    assert neo4j_uri_connection("localhost:7687") == {"scheme": "bolt", "host": "localhost", "port": 7687}


@pytest.mark.parametrize("uri", ["https://kb.example.com:7687", "neo4js://kb.example.com"])
def test_unknown_scheme_is_refused_rather_than_dialled_as_bolt(uri):
    with pytest.raises(ValueError, match="unsupported Neo4j URI scheme"):
        neo4j_uri_connection(uri)
