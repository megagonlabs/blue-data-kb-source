from blue_data.source.vector.memory_vector_source import MemoryVectorSource

from blue_data_kb_source import MemoryKBSource

_VECTOR_CLASS = "blue_data.source.vector.memory_vector_source.MemoryVectorSource"


def _fake_embedding(text: str) -> list[float]:
    return [float(len(text))] * 4


# ------------------------------------------------------------------ #
# KBSource.__init__(self, name, properties=None) -- matches            #
# DataSource's own signature; everything else lives under properties.  #
# ------------------------------------------------------------------ #

def test_no_search_by_default():
    kb = MemoryKBSource(name="t")
    assert kb.has_semantic_search is False
    assert kb._search_indexer is None


def test_search_absent_from_properties_means_no_search():
    kb = MemoryKBSource(name="t", properties={"search": {}})
    assert kb._search_indexer is None


def test_bm25_mode_from_properties_enables_search():
    kb = MemoryKBSource(name="t", properties={"search": {"mode": "bm25"}})
    assert kb._search_indexer is not None
    assert kb.has_semantic_search is False


def test_semantic_mode_builds_vector_source_declaratively():
    """A "_class" dotted path is constructed via DataSource.instantiate() --
    the fully persistable/reconstructible shape (e.g. the CLI's registry)."""
    kb = MemoryKBSource(
        name="t",
        properties={
            "search": {
                "mode": "semantic",
                "vector_dim": 4,
                "vector": {"_class": _VECTOR_CLASS, "name": "kb_vectors", "connection": {}},
                "embedding_fn": _fake_embedding,  # live callable -- see note below
            }
        },
    )
    assert kb.has_semantic_search is True
    assert isinstance(kb._search_indexer._vector_source, MemoryVectorSource)


async def test_semantic_search_declarative_construction_actually_works():
    kb = MemoryKBSource(
        name="t",
        properties={
            "search": {
                "mode": "semantic",
                "vector_dim": 4,
                "vector": {"_class": _VECTOR_CLASS, "connection": {}},
                "embedding_fn": _fake_embedding,
            }
        },
    )
    kb.create_database("main")
    person = await kb.declare_class("main", "PERSON", "A human individual")
    await kb.add_entity("main", "Alice", class_id=person)
    hits = await kb.search_data("main", "Alice", mode="semantic")
    assert any(h.name == "Alice" for h in hits)


def test_embedding_config_builds_a_default_embedding_fn():
    """properties["search"]["embedding"] (provider/model, JSON-safe config, no live
    callable) is enough on its own -- KBSource builds the embedding_fn itself."""
    kb = MemoryKBSource(
        name="t",
        properties={
            "search": {
                "mode": "semantic",
                "vector": {"_class": _VECTOR_CLASS, "connection": {}},
                "embedding": {"provider": "openai", "model": "text-embedding-3-small"},
            }
        },
    )
    assert kb._search_indexer._embedding_fn is not None


def test_live_vector_source_and_embedding_fn_objects_are_used_as_is():
    """properties["search"] may also hold already-constructed, non-JSON-safe
    objects directly -- for in-memory/programmatic use (examples, tests) where
    there's nothing to persist. "vector" (the declarative _class config) is not
    consulted at all once a live vector_source is already given."""
    explicit_vector = MemoryVectorSource("explicit")
    kb = MemoryKBSource(
        name="t",
        properties={
            "search": {
                "mode": "semantic",
                "vector_source": explicit_vector,
                "embedding_fn": _fake_embedding,
                "vector": {"_class": "not.a.real.Class"},
            }
        },
    )
    assert kb._search_indexer._vector_source is explicit_vector
    assert kb._search_indexer._embedding_fn is _fake_embedding
