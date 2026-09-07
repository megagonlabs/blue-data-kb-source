from blue_data_kb_source import MemoryKBSource
from blue_data_kb_source.resolution import NodeResolver

_VECTOR_CLASS = "blue_data.source.vector.memory_vector_source.MemoryVectorSource"


def _fake_embedding(text: str) -> list[float]:
    return [float(len(text))] * 4

# ------------------------------------------------------------------ #
# Exact tier, one method per label                                     #
# ------------------------------------------------------------------ #

async def test_exact_class_match(kb):
    class_id = await kb.declare_class("test", "PERSON", "A human individual")
    hits = await NodeResolver(kb).resolve_class("test", "PERSON")
    assert [(h.node_id, h.label, h.match_type, h.score) for h in hits] == [
        (class_id, "CLASS", "exact", None)
    ]


async def test_exact_relation_match(kb):
    await kb.declare_class("test", "PERSON", "A human")
    await kb.declare_class("test", "COMPANY", "A business")
    rel_id = await kb.declare_relation_type("test", "WORKED_AT", "Employment",
                                            domain="PERSON", range="COMPANY")
    hits = await NodeResolver(kb).resolve_relation("test", "WORKED_AT")
    assert [(h.node_id, h.match_type) for h in hits] == [(rel_id, "exact")]


async def test_exact_property_match(kb):
    class_id = await kb.declare_class("test", "PERSON", "A human")
    prop_id = await kb.declare_class_property("test", name="AGE", description="Age",
                                              class_id=class_id)
    hits = await NodeResolver(kb).resolve_property("test", "AGE")
    assert [(h.node_id, h.match_type) for h in hits] == [(prop_id, "exact")]


async def test_exact_entity_match(kb):
    entity_id = await kb.add_entity("test", "Alice")
    hits = await NodeResolver(kb).resolve_entity("test", "Alice")
    assert [(h.node_id, h.name, h.match_type) for h in hits] == [(entity_id, "Alice", "exact")]


async def test_both_same_named_entities_are_returned(kb):
    first = await kb.add_entity("test", "Alice")
    second = await kb.add_entity("test", "Alice")
    hits = await NodeResolver(kb).resolve_entity("test", "Alice")
    assert {h.node_id for h in hits} == {first, second}
    assert all(h.match_type == "exact" for h in hits)


# ------------------------------------------------------------------ #
# Normalized tier                                                      #
# ------------------------------------------------------------------ #

async def test_punctuation_and_case_normalize(kb):
    entity_id = await kb.add_entity("test", "IBM")
    hits = await NodeResolver(kb).resolve_entity("test", "i.b.m.")
    # `name` is the node's real name, not an echo of the query.
    assert [(h.node_id, h.name, h.match_type) for h in hits] == [(entity_id, "IBM", "normalized")]


async def test_accents_and_whitespace_normalize(kb):
    entity_id = await kb.add_entity("test", "Álice  Smith")
    hits = await NodeResolver(kb).resolve_entity("test", "alice smith")
    assert [(h.node_id, h.match_type) for h in hits] == [(entity_id, "normalized")]


async def test_exact_ranks_before_normalized(kb):
    exact_id = await kb.add_entity("test", "IBM")
    normalized_id = await kb.add_entity("test", "ibm")
    hits = await NodeResolver(kb).resolve_entity("test", "IBM")
    assert [(h.node_id, h.match_type) for h in hits] == [
        (exact_id, "exact"), (normalized_id, "normalized")
    ]


async def test_no_legal_suffix_dropping(kb):
    await kb.add_entity("test", "Apple Inc.")
    hits = await NodeResolver(kb).resolve_entity("test", "Apple")
    assert all(h.match_type != "normalized" for h in hits)


# ------------------------------------------------------------------ #
# Search tier                                                          #
# ------------------------------------------------------------------ #

async def test_substring_fallback_hit_without_index(kb):
    entity_id = await kb.add_entity("test", "IBM Research")
    hits = await NodeResolver(kb).resolve_entity("test", "IBM")
    assert [(h.node_id, h.name, h.match_type, h.score) for h in hits] == [
        (entity_id, "IBM Research", "search", None)
    ]


async def test_substring_fallback_matches_descriptions(kb):
    entity_id = await kb.add_entity("test", "ACME", description="A Business Machines spinoff")
    hits = await NodeResolver(kb).resolve_entity("test", "Machines")
    assert [(h.node_id, h.match_type) for h in hits] == [(entity_id, "search")]


async def test_include_search_false_suppresses_search_tier(kb):
    await kb.add_entity("test", "IBM Research")
    hits = await NodeResolver(kb).resolve_entity("test", "IBM", include_search=False)
    assert hits == []


async def test_include_search_false_still_returns_normalized_tier(kb):
    entity_id = await kb.add_entity("test", "IBM")
    hits = await NodeResolver(kb).resolve_entity("test", "ibm", include_search=False)
    assert [(h.node_id, h.match_type) for h in hits] == [(entity_id, "normalized")]


async def test_search_tier_never_repeats_an_exact_hit(kb):
    exact_id = await kb.add_entity("test", "IBM")
    sub_id = await kb.add_entity("test", "IBM Research")
    hits = await NodeResolver(kb).resolve_entity("test", "IBM")
    assert [(h.node_id, h.match_type) for h in hits] == [
        (exact_id, "exact"), (sub_id, "search")
    ]


async def test_search_tier_is_label_scoped(kb):
    class_id = await kb.declare_class("test", "WORKS", "A class")
    await kb.declare_class("test", "OTHER", "Another class")
    await kb.declare_relation_type("test", "WORKS", "A relation", domain="OTHER", range="OTHER")
    hits = await NodeResolver(kb).resolve_class("test", "WORKS")
    # By node id: the RELATION named WORKS must not come back dressed as a CLASS.
    assert [(h.node_id, h.label) for h in hits] == [(class_id, "CLASS")]


async def test_bm25_index_matches_tokens_not_substrings():
    kb = MemoryKBSource(name="resolver-bm25", properties={"search": {"mode": "bm25"}})
    kb.create_database("test")
    entity_id = await kb.add_entity("test", "Alice Smith")
    resolver = NodeResolver(kb, kb._search_indexer)
    await kb.add_fact("test", name="Smith Alice memo")
    # "Smith Alice" is not a substring of the name, so only the index can find it;
    # keyword-mode hits are ranked but scoreless by the indexer's contract. The
    # token-matching FACT must not leak into entity resolution.
    hits = await resolver.resolve_entity("test", "Smith Alice")
    assert [(h.node_id, h.match_type, h.score) for h in hits] == [(entity_id, "search", None)]


async def test_index_backed_schema_resolution_uses_the_schema_collection():
    kb = MemoryKBSource(name="resolver-schema", properties={"search": {"mode": "bm25"}})
    kb.create_database("test")
    class_id = await kb.declare_class("test", "GADGET", "A maker of widgets")
    resolver = NodeResolver(kb, kb._search_indexer)
    # Token match on the description; only the schema index can find it. A resolver
    # querying the data collection for a CLASS would come back empty.
    hits = await resolver.resolve_class("test", "widgets maker")
    assert [(h.node_id, h.label, h.match_type) for h in hits] == [(class_id, "CLASS", "search")]


async def test_index_k_admits_multiple_search_hits():
    kb = MemoryKBSource(name="resolver-k", properties={"search": {"mode": "bm25"}})
    kb.create_database("test")
    first = await kb.add_entity("test", "Alice Smith")
    second = await kb.add_entity("test", "Bob Smith")
    resolver = NodeResolver(kb, kb._search_indexer)
    hits = await resolver.resolve_entity("test", "Smith Alice", k=5)
    assert {h.node_id for h in hits} == {first, second}
    assert all(h.match_type == "search" for h in hits)


async def test_semantic_index_carries_scores():
    kb = MemoryKBSource(name="resolver-sem", properties={"search": {
        "mode": "semantic", "vector_dim": 4,
        "vector": {"_class": _VECTOR_CLASS, "connection": {}},
        "embedding_fn": _fake_embedding,
    }})
    kb.create_database("test")
    entity_id = await kb.add_entity("test", "Alice Smith")
    resolver = NodeResolver(kb, kb._search_indexer)
    hits = await resolver.resolve_entity("test", "Bob Jones")   # same length, no substring
    assert [(h.node_id, h.match_type) for h in hits] == [(entity_id, "search")]
    assert hits[0].score is not None


# ------------------------------------------------------------------ #
# Filter and cap                                                       #
# ------------------------------------------------------------------ #

async def test_class_id_filters_entities(kb):
    person = await kb.declare_class("test", "PERSON", "A human")
    company = await kb.declare_class("test", "COMPANY", "A business")
    person_alice = await kb.add_entity("test", "Alice", class_id=person)
    await kb.add_entity("test", "Alice", class_id=company)
    hits = await NodeResolver(kb).resolve_entity("test", "Alice", class_id=person)
    assert [h.node_id for h in hits] == [person_alice]


async def test_class_filter_applies_before_the_cap(kb):
    person = await kb.declare_class("test", "PERSON", "A human")
    company = await kb.declare_class("test", "COMPANY", "A business")
    for _ in range(3):
        await kb.add_entity("test", "Alice", class_id=company)
    person_alice = await kb.add_entity("test", "Alice", class_id=person)
    # The person Alice ranks past k=2 among the exact matches; the filter must see
    # the full candidate list, not the capped one.
    hits = await NodeResolver(kb).resolve_entity("test", "Alice", class_id=person, k=2)
    assert [h.node_id for h in hits] == [person_alice]


async def test_k_caps_the_ranked_list(kb):
    for i in range(7):
        await kb.add_entity("test", f"Acme Unit {i}")
    hits = await NodeResolver(kb).resolve_entity("test", "Acme", k=5)
    assert len(hits) == 5


async def test_no_match_returns_empty_list(kb):
    await kb.add_entity("test", "Alice")
    assert await NodeResolver(kb).resolve_entity("test", "Zebra") == []
