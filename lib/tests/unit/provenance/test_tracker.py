import pytest
from blue_data_kb_source.provenance.tracker import ProvenanceTracker
from blue_data_kb_source.types import ProvenanceError

# ------------------------------------------------------------------ #
# add_source                                                           #
# ------------------------------------------------------------------ #

async def test_add_source_creates_node(kb):
    src_id = await kb.add_source("test", source="doc1", name="Document 1", description="A test doc")
    node = kb.get_node("test", src_id)
    assert node is not None
    assert node["label"] == "SOURCE"
    assert node["properties"]["source"] == "doc1"
    assert node["properties"]["name"] == "Document 1"
    assert node["properties"]["description"] == "A test doc"


async def test_add_source_default_name_equals_source(kb):
    src_id = await kb.add_source("test", source="doc1")
    node = kb.get_node("test", src_id)
    assert node["properties"]["name"] == "doc1"


async def test_add_source_explicit_node_id(kb):
    src_id = await kb.add_source("test", source="doc1", node_id="my-source")
    assert src_id == "my-source"


async def test_add_source_returns_string_id(kb):
    src_id = await kb.add_source("test", source="doc1")
    assert isinstance(src_id, str)


# ------------------------------------------------------------------ #
# add_extraction                                                        #
# ------------------------------------------------------------------ #

async def test_add_extraction_creates_node(kb):
    extraction_id = await kb.add_extraction("test", text="Alice joined IBM.")
    node = kb.get_node("test", extraction_id)
    assert node is not None
    assert node["label"] == "EXTRACTION"
    assert node["properties"]["text"] == "Alice joined IBM."


async def test_add_extraction_default_name_equals_text(kb):
    extraction_id = await kb.add_extraction("test", text="Some text.")
    node = kb.get_node("test", extraction_id)
    assert node["properties"]["name"] == "Some text."


async def test_add_extraction_with_explicit_name(kb):
    extraction_id = await kb.add_extraction("test", text="Some text.", name="extraction-1")
    node = kb.get_node("test", extraction_id)
    assert node["properties"]["name"] == "extraction-1"


async def test_add_extraction_wires_to_source(kb):
    src_id = await kb.add_source("test", source="doc1")
    extraction_id = await kb.add_extraction("test", text="Alice joined IBM.", source_id=src_id)
    nbs = kb.get_neighbors("test", extraction_id, edge_type="EXTRACTED_FROM", direction="out")
    assert any(nb["node"]["id"] == src_id for nb in nbs)


async def test_add_extraction_no_source_edge_when_source_id_omitted(kb):
    extraction_id = await kb.add_extraction("test", text="Alice joined IBM.")
    nbs = kb.get_neighbors("test", extraction_id, edge_type="EXTRACTED_FROM", direction="out")
    assert nbs == []


# ------------------------------------------------------------------ #
# confidence / extraction_type                                          #
# ------------------------------------------------------------------ #

async def test_add_extraction_stores_confidence(kb):
    extraction_id = await kb.add_extraction("test", text="Alice joined IBM.", confidence=0.9)
    node = kb.get_node("test", extraction_id)
    assert node["properties"]["confidence"] == 0.9


async def test_add_extraction_confidence_defaults_to_none(kb):
    extraction_id = await kb.add_extraction("test", text="Alice joined IBM.")
    node = kb.get_node("test", extraction_id)
    assert node["properties"]["confidence"] is None


async def test_add_extraction_stores_extraction_type(kb):
    extraction_id = await kb.add_extraction(
        "test", text="Q937: employer IBM", extraction_type="external_record"
    )
    node = kb.get_node("test", extraction_id)
    assert node["properties"]["extraction_type"] == "external_record"


async def test_add_extraction_extraction_type_defaults_to_text_span(kb):
    extraction_id = await kb.add_extraction("test", text="Alice joined IBM.")
    node = kb.get_node("test", extraction_id)
    assert node["properties"]["extraction_type"] == "text_span"


async def test_extraction_type_is_not_validated_at_runtime(kb):
    # The Literal is advisory, as with literal_type: `inference` is reserved in the
    # data model but not yet a member, and nothing rejects it at write time.
    extraction_id = await kb.add_extraction(
        "test",
        text="Derived from two other facts.",
        extraction_type="inference",  # type: ignore[arg-type]
    )
    node = kb.get_node("test", extraction_id)
    assert node["properties"]["extraction_type"] == "inference"


async def test_add_extraction_accepts_confidence_bounds(kb):
    low_id  = await kb.add_extraction("test", text="Low.",  confidence=0.0)
    high_id = await kb.add_extraction("test", text="High.", confidence=1.0)
    assert kb.get_node("test", low_id)["properties"]["confidence"] == 0.0
    assert kb.get_node("test", high_id)["properties"]["confidence"] == 1.0


async def test_add_extraction_rejects_confidence_out_of_range(kb):
    with pytest.raises(ProvenanceError, match=r"\[0\.0, 1\.0\]"):
        await kb.add_extraction("test", text="Too high.", confidence=1.5)
    with pytest.raises(ProvenanceError, match=r"\[0\.0, 1\.0\]"):
        await kb.add_extraction("test", text="Too low.", confidence=-0.1)


async def test_add_extraction_with_element_id_stamps_confidence_and_type(kb):
    entity_id = await kb.add_entity("test", "Alice")
    extraction_id = await kb.add_extraction(
        "test", text="Alice joined IBM.", element_id=entity_id, confidence=0.7,
        extraction_type="assertion",
    )
    node = kb.get_node("test", extraction_id)
    assert node["properties"]["confidence"] == 0.7
    assert node["properties"]["extraction_type"] == "assertion"


async def test_add_entity_with_text_stamps_default_confidence_and_type(kb):
    entity_id = await kb.add_entity("test", "Alice", text="Alice is a software engineer.")
    nbs = kb.get_neighbors("test", entity_id, edge_type="EXTRACTED_FROM", direction="out")
    props = nbs[0]["node"]["properties"]
    assert props["confidence"] is None
    assert props["extraction_type"] == "text_span"


# ------------------------------------------------------------------ #
# track (full provenance chain via add_entity text= argument)          #
# ------------------------------------------------------------------ #

async def test_add_entity_with_text_creates_extraction_node(kb):
    entity_id = await kb.add_entity("test", "Alice", text="Alice is a software engineer.")
    nbs = kb.get_neighbors("test", entity_id, edge_type="EXTRACTED_FROM", direction="out")
    assert len(nbs) == 1
    extraction = nbs[0]["node"]
    assert extraction["label"] == "EXTRACTION"
    assert extraction["properties"]["text"] == "Alice is a software engineer."


async def test_provenance_chain_entity_to_extraction_to_source(kb):
    src_id    = await kb.add_source("test", source="doc1")
    entity_id = await kb.add_entity("test", "Alice",
                                     text="Alice is a software engineer.", source_id=src_id)

    # entity -[EXTRACTED_FROM]-> extraction
    nbs = kb.get_neighbors("test", entity_id, edge_type="EXTRACTED_FROM", direction="out")
    assert len(nbs) == 1
    extraction = nbs[0]["node"]
    assert extraction["properties"]["text"] == "Alice is a software engineer."

    # extraction -[EXTRACTED_FROM]-> source
    src_nbs = kb.get_neighbors("test", extraction["id"], edge_type="EXTRACTED_FROM", direction="out")
    assert len(src_nbs) == 1
    assert src_nbs[0]["node"]["id"] == src_id


async def test_provenance_chain_without_source_has_no_second_edge(kb):
    entity_id = await kb.add_entity("test", "Alice", text="Some text.")
    nbs = kb.get_neighbors("test", entity_id, edge_type="EXTRACTED_FROM", direction="out")
    assert len(nbs) == 1
    extraction_nbs = kb.get_neighbors("test", nbs[0]["node"]["id"],
                                       edge_type="EXTRACTED_FROM", direction="out")
    assert extraction_nbs == []


async def test_provenance_chain_for_fact(kb):
    src_id   = await kb.add_source("test", source="doc1")
    alice_id = await kb.add_entity("test", "Alice")
    ibm_id   = await kb.add_entity("test", "IBM")
    fact_id  = await kb.add_fact("test",
                                  head_entity_id=alice_id,
                                  tail_entity_id=ibm_id,
                                  text="Alice joined IBM.", source_id=src_id)

    nbs = kb.get_neighbors("test", fact_id, edge_type="EXTRACTED_FROM", direction="out")
    assert len(nbs) == 1
    extraction = nbs[0]["node"]
    assert extraction["properties"]["text"] == "Alice joined IBM."

    src_nbs = kb.get_neighbors("test", extraction["id"], edge_type="EXTRACTED_FROM", direction="out")
    assert any(nb["node"]["id"] == src_id for nb in src_nbs)


async def test_provenance_chain_for_literal(kb):
    src_id    = await kb.add_source("test", source="doc1")
    entity_id = await kb.add_entity("test", "Alice")
    lit_id    = await kb.add_entity_property_value("test", "1990-03-15",
                                                    entity_id=entity_id,
                                                    text="Alice was born on 1990-03-15.", source_id=src_id)

    nbs = kb.get_neighbors("test", lit_id, edge_type="EXTRACTED_FROM", direction="out")
    assert len(nbs) == 1
    extraction = nbs[0]["node"]
    assert extraction["properties"]["text"] == "Alice was born on 1990-03-15."

    src_nbs = kb.get_neighbors("test", extraction["id"], edge_type="EXTRACTED_FROM", direction="out")
    assert any(nb["node"]["id"] == src_id for nb in src_nbs)


# ------------------------------------------------------------------ #
# add_extraction(element_id=...) — create + link in one call           #
# ------------------------------------------------------------------ #

async def test_add_extraction_with_element_id_wires_edge(kb):
    entity_id = await kb.add_entity("test", "Alice")
    extraction_id = await kb.add_extraction("test", text="Some text.", element_id=entity_id)
    nbs = kb.get_neighbors("test", entity_id, edge_type="EXTRACTED_FROM", direction="out")
    assert any(nb["node"]["id"] == extraction_id for nb in nbs)


async def test_add_extraction_without_element_id_does_not_link(kb):
    entity_id = await kb.add_entity("test", "Alice")
    await kb.add_extraction("test", text="Some text.")
    nbs = kb.get_neighbors("test", entity_id, edge_type="EXTRACTED_FROM", direction="out")
    assert nbs == []


async def test_add_extraction_with_element_id_accumulates_alongside_existing_edge(kb):
    entity_id = await kb.add_entity("test", "Alice", text="Alice joined IBM.")
    await kb.add_extraction("test", text="Alice was promoted at IBM.", element_id=entity_id)
    nbs = kb.get_neighbors("test", entity_id, edge_type="EXTRACTED_FROM", direction="out")
    texts = {nb["node"]["properties"]["text"] for nb in nbs}
    assert len(nbs) == 2
    assert texts == {"Alice joined IBM.", "Alice was promoted at IBM."}


async def test_add_extraction_with_element_id_does_not_duplicate_the_entity_node(kb):
    entity_id = await kb.add_entity("test", "Alice", description="Original description")
    await kb.add_extraction("test", text="More evidence.", element_id=entity_id)
    node = kb.get_node("test", entity_id)
    assert node["properties"]["description"] == "Original description"
    assert len(kb.match_nodes("test", label="ENTITY")) == 1


async def test_add_extraction_with_element_id_and_source_id_links_both(kb):
    entity_id = await kb.add_entity("test", "Alice")
    src_id = await kb.add_source("test", source="doc2")
    extraction_id = await kb.add_extraction(
        "test", text="New evidence.", element_id=entity_id, source_id=src_id
    )
    nbs = kb.get_neighbors("test", entity_id, edge_type="EXTRACTED_FROM", direction="out")
    assert any(nb["node"]["id"] == extraction_id for nb in nbs)
    src_nbs = kb.get_neighbors("test", extraction_id, edge_type="EXTRACTED_FROM", direction="out")
    assert any(nb["node"]["id"] == src_id for nb in src_nbs)


async def test_tracker_add_extraction_wires_element_and_source_itself(kb):
    # the tracker owns both links now; the facade only delegates
    entity_id = await kb.add_entity("test", "Alice")
    src_id = await kb.add_source("test", source="doc3")
    tracker = ProvenanceTracker(kb)
    extraction_id = await tracker.add_extraction(
        "test", "Direct evidence.", source_id=src_id, element_id=entity_id
    )
    def out(n):
        return {nb["node"]["id"] for nb in kb.get_neighbors(
            "test", n, edge_type="EXTRACTED_FROM", direction="out")}
    assert extraction_id in out(entity_id)
    assert src_id in out(extraction_id)


# ------------------------------------------------------------------ #
# attach_extraction                                                    #
# ------------------------------------------------------------------ #

def _collect_events(kb, database: str) -> list[dict]:
    events: list[dict] = []
    kb.add_change_hook(database, events.append)
    return events


def _extractions_of(kb, database: str, node_id: str) -> list[str]:
    return [nb["node"]["id"] for nb in kb.get_neighbors(
        database, node_id, edge_type=kb.EXTRACTED_FROM, direction="out")]


async def test_attach_extraction_wires_an_extraction_created_up_front(kb):
    person = await kb.declare_class("test", "PERSON", "A human individual")
    alice = await kb.add_entity("test", "Alice", class_id=person)
    extraction = await kb.add_extraction("test", "Alice is an engineer.")
    assert _extractions_of(kb, "test", alice) == []

    await kb.attach_extraction("test", alice, extraction)

    assert _extractions_of(kb, "test", alice) == [extraction]
    assert len(kb.match_nodes("test", label="EXTRACTION")) == 1


async def test_attach_extraction_deduplicates_and_fires_hook_once(kb):
    person = await kb.declare_class("test", "PERSON", "A human individual")
    alice = await kb.add_entity("test", "Alice", class_id=person)
    extraction = await kb.add_extraction("test", "Alice is an engineer.")
    events = _collect_events(kb, "test")

    await kb.attach_extraction("test", alice, extraction)
    ev = next(e for e in events if e["type"] == "extraction_attached")
    assert ev["element_id"] == alice
    assert ev["extraction_id"] == extraction

    await kb.attach_extraction("test", alice, extraction)
    assert len([e for e in events if e["type"] == "extraction_attached"]) == 1
    assert _extractions_of(kb, "test", alice) == [extraction]


async def test_attach_extraction_rejects_wrong_labels(kb):
    person = await kb.declare_class("test", "PERSON", "A human individual")
    alice = await kb.add_entity("test", "Alice", class_id=person)
    extraction = await kb.add_extraction("test", "Alice is an engineer.")
    with pytest.raises(ProvenanceError):
        await kb.attach_extraction("test", person, extraction)  # element is a CLASS
    with pytest.raises(ProvenanceError):
        await kb.attach_extraction("test", alice, alice)        # extraction_id is an ENTITY


async def test_attach_extraction_accepts_fact_and_literal_elements(kb):
    person  = await kb.declare_class("test", "PERSON", "A human individual")
    company = await kb.declare_class("test", "COMPANY", "A business organization")
    worked  = await kb.declare_relation_type(
        "test", "WORKED_AT", "Employment", domain="PERSON", range="COMPANY"
    )
    age   = await kb.declare_class_property("test", "AGE", "Age in years", class_id=person)
    alice = await kb.add_entity("test", "Alice", class_id=person)
    ibm   = await kb.add_entity("test", "IBM", class_id=company)
    fact  = await kb.add_fact("test", relation_id=worked, head_entity_id=alice, tail_entity_id=ibm)
    await kb.add_entity_property_value("test", 30, entity_id=alice, property_id=age)
    literal = kb.match_nodes("test", label="LITERAL")[0]["id"]
    extraction = await kb.add_extraction("test", "Alice, 30, joined IBM.")

    await kb.attach_extraction("test", fact, extraction)
    await kb.attach_extraction("test", literal, extraction)

    assert _extractions_of(kb, "test", fact) == [extraction]
    assert _extractions_of(kb, "test", literal) == [extraction]


async def test_attach_extraction_works_on_a_tracker_without_a_notifier(kb):
    person = await kb.declare_class("test", "PERSON", "A human individual")
    alice = await kb.add_entity("test", "Alice", class_id=person)
    extraction = await kb.add_extraction("test", "Alice is an engineer.")
    tracker = ProvenanceTracker(kb)   # no notifier, as a bare component

    await tracker.attach_extraction("test", alice, extraction)
    await tracker.attach_extraction("test", alice, extraction)   # dedupe path, still no notifier

    assert _extractions_of(kb, "test", alice) == [extraction]
