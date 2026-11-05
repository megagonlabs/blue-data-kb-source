"""
LangGraph + blue-data-kb-source: MCP-based integration.

A ReAct agent that uses the real blue-data-kb-mcp server as tools via
langchain-mcp-adapters. No KBSource-specific code on the LangGraph side at
all -- the agent discovers all 26 tools (schema declare/search, expand_schema,
entity/fact/property writes, get_metadata, answer/validate/enrich, import_text,
add_source) from the running MCP server and picks which ones to call itself.

Contrast with langgraph_direct_demo.py, which hand-builds a fixed 3-node
pipeline directly against KBSource's Python API -- this demo has no fixed
pipeline at all; the LLM's own ReAct loop decides which tools to call and in
what order.

Run:
    pip install langgraph langchain-openai langchain-mcp-adapters
    pip install -e platform/mcp/
    export OPENAI_API_KEY=sk-...
    python3 langgraph_mcp_demo.py

The blue-data-kb-mcp server is launched automatically as a subprocess (stdio
transport), pointed at a scratch directory via BLUE_DATA_KB_SOURCE_DATA_DIR
so it never touches ~/.blue-kb. BLUE_DATA_KB_SOURCE_SEARCH=bm25 turns on ranked
keyword search for the search_schema/search_data tools -- no embedding API
calls needed for that part, only the ReAct agent's own reasoning uses the
model.
"""
import asyncio
import os
import shutil
import tempfile

from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent


async def main() -> None:
    tmp = tempfile.mkdtemp(prefix="blue-data-kb-mcp-demo-")
    print(f"Scratch KB directory: {tmp}\n")

    model = ChatOpenAI(model="gpt-4o-mini", temperature=0)

    # Launch blue-data-kb-mcp as a subprocess, pointed at the scratch directory.
    client = MultiServerMCPClient({
        "kb": {
            "command": "blue-data-kb-mcp",
            "args": [],
            "transport": "stdio",
            "env": {
                **os.environ,
                "BLUE_DATA_KB_SOURCE_DATA_DIR": tmp,
                "BLUE_DATA_KB_SOURCE_SEARCH": "bm25",
            },
        }
    })

    tools = await client.get_tools()
    print(f"Loaded {len(tools)} tools from blue-data-kb-mcp: {[t.name for t in tools]}\n")

    agent = create_react_agent(model, tools)

    queries = [
        "Declare a PERSON class and a COMPANY class, then a WORKED_AT relation "
        "between them (domain PERSON, range COMPANY).",
        "Add Alice as a PERSON described as a software engineer and add IBM as a "
        "COMPANY. Once you have their ids, record that Alice worked at IBM.",
        "Search the knowledge base for 'Alice' and tell me what you find.",
        "Using the knowledge base, answer: where does Alice work?",
    ]

    try:
        for query in queries:
            print(f"Query: {query}")
            result = await agent.ainvoke({
                "messages": [{"role": "user", "content": query}]
            })
            answer = result["messages"][-1].content
            print(f"Answer: {answer}\n")
    finally:
        shutil.rmtree(tmp)
        print(f"Cleaned up {tmp}")


if __name__ == "__main__":
    asyncio.run(main())
