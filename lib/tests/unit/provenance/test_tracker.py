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
# add_extracted_from                                                    #
# ------------------------------------------------------------------ #

async def test_add_extracted_from_wires_edge(kb):
    entity_id     = await kb.add_entity("test", "Alice")
    extraction_id = await kb.add_extraction("test", text="Some text.")
    await kb.add_extracted_from("test", entity_id, extraction_id)
    nbs = kb.get_neighbors("test", entity_id, edge_type="EXTRACTED_FROM", direction="out")
    assert any(nb["node"]["id"] == extraction_id for nb in nbs)


async def test_add_extracted_from_returns_none(kb):
    entity_id     = await kb.add_entity("test", "Alice")
    extraction_id = await kb.add_extraction("test", text="Some text.")
    result = await kb.add_extracted_from("test", entity_id, extraction_id)
    assert result is None
