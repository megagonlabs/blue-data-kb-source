"""
LangGraph + blue-data-kb-source: direct Python API integration.

A 3-node LangGraph pipeline built directly on top of KBSource's low-level
read primitives (search_data/search_schema/get_metadata) rather than its
own built-in answer() agent loop -- this is the "build your own agent"
story, contrasted with langgraph_mcp_demo.py's "auto-discover tools from an
existing server" story:

  discover  ->  select  ->  respond

  1. discover -- KBSource.search_data()/search_schema()  BM25 keyword search
                 over the KB, no LLM call, no API key needed for this step
  2. select   -- an LLM call picks which search hits are relevant to the
                 question and pulls get_metadata() for each one
  3. respond  -- an LLM call composes the final natural-language answer
                 from the selected hits + their metadata

Contrast with langgraph_mcp_demo.py, which has no KBSource-specific code at
all -- a ReAct agent free-chooses among tools auto-discovered from the real
blue-data-kb-mcp server.

Run:
    pip install langgraph langchain-openai
    export OPENAI_API_KEY=sk-...
    python3 langgraph_direct_demo.py
"""
import asyncio
import json
from typing import TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from blue_data_kb_source import MemoryKBSource

DIVIDER = "-" * 60


# --------------------------------------------------------------------------- #
# Setup: seed a small KB with BM25 search enabled (no API key needed for this)
# --------------------------------------------------------------------------- #

kb = MemoryKBSource(name="direct-demo", properties={"search": {"mode": "bm25"}})
kb.create_database("main")


async def seed() -> None:
    person = await kb.declare_class("main", "PERSON", "A human individual")
    company = await kb.declare_class("main", "COMPANY", "A business organization")
    worked_at = await kb.declare_relation_type(
        "main", "WORKED_AT", "Employment relationship", domain="PERSON", range="COMPANY"
    )
    title_prop = await kb.declare_class_property("main", "TITLE", "Job title", class_id=person)

    src = await kb.add_source("main", source="resume.pdf", name="Alice's resume")
    alice = await kb.add_entity(
        "main", "Alice", class_id=person, description="Software engineer",
        text="Alice is a software engineer.", source_id=src,
    )
    ibm = await kb.add_entity("main", "IBM", class_id=company, description="Technology company")
    await kb.add_entity_property_value(
        "main", "Senior Software Engineer", entity_id=alice, property_id=title_prop
    )
    await kb.add_fact(
        "main", head_entity_id=alice, tail_entity_id=ibm, relation_id=worked_at,
        text="Alice has worked at IBM since 2019.", source_id=src,
    )


model: ChatOpenAI | None = None  # constructed in main(), after the KB is seeded


# --------------------------------------------------------------------------- #
# Agent state passed between nodes
# --------------------------------------------------------------------------- #

class State(TypedDict):
    question: str            # natural language question from the user
    hits: list[dict]         # raw search_data() results
    selected: list[dict]     # hits the LLM decided are relevant, with metadata attached
    answer: str               # final composed answer


# --------------------------------------------------------------------------- #
# Node 1: discover -- KBSource.search_data(), a raw BM25 keyword search, no LLM
# --------------------------------------------------------------------------- #

async def discover(state: State) -> dict:
    hits = await kb.search_data("main", state["question"], k=5)
    print(f"[discover] search_data({state['question']!r}) -> {len(hits)} hit(s)")
    return {"hits": [{"id": h.id, "type": h.type, "name": h.name, "description": h.description} for h in hits]}


# --------------------------------------------------------------------------- #
# Node 2: select -- LLM picks which hits matter, then get_metadata() on each
# --------------------------------------------------------------------------- #

async def select(state: State) -> dict:
    if not state["hits"]:
        return {"selected": []}

    prompt = (
        "Given this question and these search hits from a knowledge base, "
        "return a JSON list of the ids of the hits relevant to answering it.\n\n"
        f"Question: {state['question']}\n\nHits:\n{json.dumps(state['hits'], indent=2)}"
    )
    raw = model.invoke(prompt).content
    relevant_ids = json.loads(raw)
    print(f"[select] LLM chose {len(relevant_ids)} relevant hit(s): {relevant_ids}")

    selected = []
    for hit in state["hits"]:
        if hit["id"] in relevant_ids:
            meta = await kb.get_metadata("main", hit["id"])
            selected.append({**hit, "metadata": meta})
    return {"selected": selected}


# --------------------------------------------------------------------------- #
# Node 3: respond -- LLM composes the final answer from selected hits + metadata
# --------------------------------------------------------------------------- #

async def respond(state: State) -> dict:
    prompt = (
        f"Answer this question using only the facts below.\n\n"
        f"Question: {state['question']}\n\nFacts:\n{json.dumps(state['selected'], indent=2)}"
    )
    answer = model.invoke(prompt).content
    print(f"[respond] {answer}")
    return {"answer": answer}


# --------------------------------------------------------------------------- #
# Build the graph: discover -> select -> respond -> END
# --------------------------------------------------------------------------- #

graph = StateGraph(State)
graph.add_node("discover", discover)
graph.add_node("select", select)
graph.add_node("respond", respond)

graph.set_entry_point("discover")
graph.add_edge("discover", "select")
graph.add_edge("select", "respond")
graph.add_edge("respond", END)

agent = graph.compile()


async def main() -> None:
    global model

    print(DIVIDER)
    print("1. Seed the KB")
    print(DIVIDER)
    await seed()
    print("Declared PERSON/COMPANY/WORKED_AT/TITLE and added Alice, IBM, and their fact")

    print()
    print(DIVIDER)
    print("2. Run the discover -> select -> respond pipeline")
    print(DIVIDER)

    model = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    question = "Where does Alice work and what is her title?"
    print(f"Question: {question}\n")

    result = await agent.ainvoke({"question": question, "hits": [], "selected": [], "answer": ""})

    print()
    print("Final answer:", result["answer"])
    print()
    print("Demo complete.")


asyncio.run(main())
