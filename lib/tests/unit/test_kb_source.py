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


# ------------------------------------------------------------------ #
# attach events keep the search index fresh                            #
# ------------------------------------------------------------------ #

def _indexed_text(kb, database: str, node_id: str) -> str | None:
    for docs in kb._search_indexer._corpus.get(database, {}).values():
        if node_id in docs:
            return docs[node_id]
    return None


async def test_attaching_a_class_reindexes_a_classless_entity():
    """The BM25 corpus folds an entity's class name into its indexed text, so the
    class an entity gains after creation has to reindex it — otherwise the entity
    stays unfindable by that class until someone calls `reindex`."""
    kb = MemoryKBSource(name="t", properties={"search": {"mode": "bm25"}})
    kb.create_database("test")
    employee = await kb.declare_class("test", "EMPLOYEE", "An employed person")
    zed = await kb.add_entity("test", "Zed")

    assert _indexed_text(kb, "test", zed) == "Zed"
    await kb.attach_entity_class("test", zed, employee)
    assert _indexed_text(kb, "test", zed) == "Zed EMPLOYEE"


async def test_a_second_class_is_not_folded_into_the_indexed_text():
    """Pins a known limitation, not a desired one: the indexer folds in only the
    *first* IS_INSTANCE_OF neighbour (`cls_nbs[0]`), so an entity that already has
    a class gains nothing searchable from a second one. The event fires and the
    entity is reindexed either way — the gap is in what `_entity_text` reads."""
    kb = MemoryKBSource(name="t", properties={"search": {"mode": "bm25"}})
    kb.create_database("test")
    person   = await kb.declare_class("test", "PERSON", "A human individual")
    employee = await kb.declare_class("test", "EMPLOYEE", "An employed person")
    alice = await kb.add_entity("test", "Alice", class_id=person)

    await kb.attach_entity_class("test", alice, employee)
    assert _indexed_text(kb, "test", alice) == "Alice PERSON"


async def test_attaching_an_extraction_reindexes_the_element():
    """The corpus folds in provenance text, so wiring an extraction to an item has to
    reindex that item."""
    kb = MemoryKBSource(name="t", properties={"search": {"mode": "bm25"}})
    kb.create_database("test")
    person = await kb.declare_class("test", "PERSON", "A human individual")
    ann = await kb.add_entity("test", "Ann", class_id=person)
    extraction = await kb.add_extraction("test", "Ann performs oboe recitals.")

    assert _indexed_text(kb, "test", ann) == "Ann PERSON"
    await kb.attach_extraction("test", ann, extraction)
    assert _indexed_text(kb, "test", ann) == "Ann PERSON Ann performs oboe recitals."


async def test_attaching_a_property_reindexes_its_new_owner():
    """A class's or relation's indexed text lists its properties, so `attach_property`
    has to reindex the owner it was attached to — `declare_*_property` already does."""
    kb = MemoryKBSource(name="t", properties={"search": {"mode": "bm25"}})
    kb.create_database("test")
    person  = await kb.declare_class("test", "PERSON", "A human individual")
    company = await kb.declare_class("test", "COMPANY", "A business organization")
    worked  = await kb.declare_relation_type(
        "test", "WORKED_AT", "Employment", domain="PERSON", range="COMPANY"
    )
    start = await kb.declare_class_property("test", "START_DATE", "Start of employment",
                                            class_id=person)

    assert _indexed_text(kb, "test", company) == "COMPANY CLASS A business organization"
    await kb.attach_property("test", company, start)
    assert _indexed_text(kb, "test", company) == (
        "COMPANY CLASS A business organization START_DATE: Start of employment"
    )

    before = _indexed_text(kb, "test", worked)
    await kb.attach_property("test", worked, start)
    assert _indexed_text(kb, "test", worked) == before + " START_DATE: Start of employment"


# ------------------------------------------------------------------ #
# execute_query — confidence / extraction_type forwarding              #
# ------------------------------------------------------------------ #

def test_execute_query_add_extraction_forwards_confidence_and_type(kb):
    extraction_id = kb.execute_query(
        {"op": "KB.ADD_EXTRACTION", "text": "Alice joined IBM.",
          "confidence": 0.8, "extraction_type": "assertion"},
        database="test",
    )
    props = kb.get_node("test", extraction_id)["properties"]
    assert props["confidence"] == 0.8
    assert props["extraction_type"] == "assertion"


def test_execute_query_add_extraction_defaults_match_the_method(kb):
    extraction_id = kb.execute_query(
        {"op": "KB.ADD_EXTRACTION", "text": "Alice joined IBM."}, database="test"
    )
    props = kb.get_node("test", extraction_id)["properties"]
    assert props["confidence"] is None
    assert props["extraction_type"] == "text_span"


def test_execute_query_creator_ops_forward_confidence_and_type(kb):
    def q(**kw):
        return kb.execute_query(kw, database="test")

    person_id  = q(op="KB.DECLARE_CLASS", name="PERSON", description="")
    company_id = q(op="KB.DECLARE_CLASS", name="COMPANY", description="")
    rel_id     = q(op="KB.DECLARE_RELATION", name="WORKED_AT", description="",
                   domain="PERSON", range="COMPANY")
    age_id     = q(op="KB.DECLARE_CLASS_PROPERTY", name="AGE", description="", class_id=person_id)
    start_id   = q(op="KB.DECLARE_RELATION_PROPERTY", name="START_DATE", description="", relation_id=rel_id)
    tail_id    = q(op="KB.ADD_ENTITY", name="IBM", class_id=company_id)
    entity_id = q(op="KB.ADD_ENTITY", name="Alice", class_id=person_id, text="Alice joined IBM.",
                  confidence=0.9, extraction_type="assertion")
    fact_id   = q(op="KB.ADD_FACT", head_entity_id=entity_id, tail_entity_id=tail_id, relation_id=rel_id,
                  text="Alice joined IBM.", confidence=0.8, extraction_type="external_record")
    lit_e     = q(op="KB.ADD_ENTITY_PROPERTY", value="28", entity_id=entity_id,
                  property_id=age_id, text="Alice is 28.", confidence=0.7)
    lit_f     = q(op="KB.ADD_FACT_PROPERTY", value="1998", fact_id=fact_id,
                  property_id=start_id, text="In 1998.", confidence=0.6)

    expected = {entity_id: (0.9, "assertion"), fact_id: (0.8, "external_record"),
                lit_e: (0.7, "text_span"), lit_f: (0.6, "text_span")}
    for node_id, (confidence, extraction_type) in expected.items():
        nbs = kb.get_neighbors("test", node_id, edge_type="EXTRACTED_FROM", direction="out")
        props = nbs[0]["node"]["properties"]
        assert props["confidence"] == confidence
        assert props["extraction_type"] == extraction_type


def test_execute_query_extraction_ops_forward_confidence_and_type(kb):
    def q(**kw):
        return kb.execute_query(kw, database="test")

    person_id = q(op="KB.DECLARE_CLASS", name="PERSON", description="")
    company_id = q(op="KB.DECLARE_CLASS", name="COMPANY", description="")
    rel_id = q(op="KB.DECLARE_RELATION", name="WORKED_AT", description="",
               domain="PERSON", range="COMPANY")
    age_id = q(op="KB.DECLARE_CLASS_PROPERTY", name="AGE", description="", class_id=person_id)
    start_id = q(op="KB.DECLARE_RELATION_PROPERTY", name="START_DATE", description="", relation_id=rel_id)
    tail_id = q(op="KB.ADD_ENTITY", name="IBM", class_id=company_id)
    entity_id = q(op="KB.ADD_ENTITY", name="Alice", class_id=person_id)
    fact_id   = q(op="KB.ADD_FACT", head_entity_id=entity_id, tail_entity_id=tail_id, relation_id=rel_id)
    q(op="KB.ADD_ENTITY_PROPERTY", value="28", entity_id=entity_id, property_id=age_id)
    q(op="KB.ADD_FACT_PROPERTY", value="1998", fact_id=fact_id, property_id=start_id)

    cases = [
        dict(op="KB.ADD_ENTITY_EXTRACTION", entity_id=entity_id, text="Alice is an engineer."),
        dict(op="KB.ADD_FACT_EXTRACTION", fact_id=fact_id, text="Alice joined IBM."),
        dict(op="KB.ADD_ENTITY_PROPERTY_EXTRACTION", entity_id=entity_id,
             property_id=age_id, text="Alice is 28."),
        dict(op="KB.ADD_FACT_PROPERTY_EXTRACTION", fact_id=fact_id,
             property_id=start_id, text="It was 1998."),
    ]
    for case in cases:
        ext_id = q(confidence=0.42, extraction_type="assertion", **case)
        props = kb.get_node("test", ext_id)["properties"]
        assert props["confidence"] == 0.42, case["op"]
        assert props["extraction_type"] == "assertion", case["op"]
