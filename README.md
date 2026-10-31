# blue-data-kb-source

A typed knowledge-base graph layer with schema management, LLM-based knowledge extraction, and natural language querying.

Agents need knowledge they can trust and explain: a schema that says what kinds of things exist, facts that trace back to the document they came from, and answers that change when the underlying evidence does. blue-data-kb-source keeps that knowledge in a typed graph that grows from free text and structured data alike, and lets people and LLM agents query it in plain language, through a Python library, the `blue-kb` CLI, or an MCP server.

→ **[Full documentation](docs/README.md)** · **[Quick start](docs/quickstart.md)** · **[Installation](docs/installation.md)** · **[Examples](examples/README.md)** · **[CLI](docs/cli.md)** · **[MCP](docs/mcp.md)**

## What it does

- **Model** domain knowledge with a typed schema: classes, relations, properties, and subclass hierarchies, with integrity checks on every declaration, rename, and merge.
- **Populate** the graph with entities, facts, and property values, each linked to the source text and document it came from, with confidence and timestamps.
- **Extract** knowledge from free text with an LLM agent that grows the schema where the text needs it, then fills in instances that fit it.
- **Ingest** structured knowledge by name: `assert` operations resolve, deduplicate, and attach evidence instead of creating duplicates.
- **Query** the knowledge base in plain language: answer questions, validate statements, and enrich topics by walking the graph, with provenance on every result.
- **Connect** LLM agents through the MCP server, or work from the `blue-kb` CLI; storage is in memory, in local files, or in Neo4j.

See [docs/features.md](docs/features.md) for the full list with examples, and [examples/](examples/README.md) for runnable scripts.

## Repository layout

```
blue-data-kb-source/
├── lib/                        # Python library (blue-data-kb-source)
│   ├── src/blue_data_kb_source/
│   └── tests/
│       ├── unit/               # fast offline tests (optionally against Neo4j)
│       └── benchmarks/         # report-only LLM benchmarks
├── platform/
│   ├── cli/                    # Command-line interface (blue-kb)
│   │   ├── src/blue_data_kb_cli/
│   │   └── tests/
│   ├── mcp/                    # MCP server (blue-data-kb-mcp)
│   │   ├── src/blue_data_kb_mcp/
│   │   └── tests/
│   └── wordnet-mcp/            # WordNet MCP server (blue-data-wordnet-mcp)
│       ├── src/blue_data_wordnet_mcp/
│       └── tests/
├── scripts/                    # install.sh — installs every package, editable
├── docs/                       # User-facing documentation (start at docs/README.md)
├── examples/                   # Runnable examples (start at examples/README.md)
└── design/                     # Architecture and design-process documents
```

## Requirements

- Python 3.12 or 3.13
- The sibling [`blue-data`](https://github.com/megagon-internal/blue-data) repository, cloned beside this one (`../blue-data`); its `lib/` provides the graph and vector storage layer
- An API key for the LLM features (extraction, schema expansion, `answer` / `validate` / `enrich`): OpenAI by default, or another provider through `BLUE_DATA_LLM_PROVIDER` (`anthropic` with `ANTHROPIC_API_KEY`; see [the CLI's parameters](docs/cli.md#parameters))
- Optional: a Neo4j 5 server, for the Neo4j backend

```bash
export OPENAI_API_KEY=sk-...
```

## Quick start

```bash
# Install blue-data (from ../blue-data), the library with all extras, the CLI and both MCP servers
bash scripts/install.sh

# Create a database and a small schema
blue-kb database register main --search bm25 && blue-kb database select main
blue-kb schema class add PERSON "A human individual"
blue-kb schema class add COMPANY "A business organization"
blue-kb schema relation add WORKED_AT "Employment relationship" --domain PERSON --range COMPANY

# Add a source, then entities and a fact that carry their evidence
blue-kb data source add resume --name "Alice's résumé"
blue-kb data entity add --name Alice --class PERSON --text "Alice is a software engineer." --source resume
blue-kb data entity add --name IBM --class COMPANY
blue-kb data fact add --head Alice --relation WORKED_AT --tail IBM \
    --text "Alice joined IBM in June 1998." --source resume
blue-kb data list --show-provenance

# Extract from free text, then ask in plain language (needs the provider's API key)
blue-kb data extract "Alice left IBM in 2005 to join Google as a staff engineer." \
    --query "Where has Alice worked?"
blue-kb data answer "Where has Alice worked, and since when?" --format nl
```

Databases live under `~/.blue-kb/` by default. The full walkthrough, with expected output at each step, the Python-library equivalent, and connecting an agent over MCP, is in **[docs/quickstart.md](docs/quickstart.md)**.

## Packages

| Package | Description |
|---|---|
| [lib/](lib/README.md) | `blue-data-kb-source`: the core Python library (`KBSource` with memory, file, and Neo4j backends) |
| [platform/cli/](platform/cli/README.md) | `blue-data-kb-cli`: the command-line tool, installs `blue-kb` |
| [platform/mcp/](platform/mcp/README.md) | `blue-data-kb-mcp`: MCP server for LLM agents, installs `blue-data-kb-mcp` |
| [platform/wordnet-mcp/](platform/wordnet-mcp/README.md) | `blue-data-wordnet-mcp`: standalone MCP server for WordNet lookups, installs `blue-data-wordnet-mcp`; optionally used by schema expansion to ground new names and hierarchy placement |

---

## Code of Conduct

This project adheres to the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md).
By participating, you are expected to uphold this code. Report unacceptable behavior
to contact_oss@megagon.ai.

---

## Disclosures

This software may include, incorporate, or access open source software (OSS) components,
datasets and other third party components, including those identified below. The license terms
respectively governing the datasets and third-party components continue to govern those
portions, and you agree to those license terms may limit any distribution, use, and copying.
You may use any OSS components under the terms of their respective licenses, which may
include BSD 3, Apache 2.0, and other licenses. In the event of conflicts between Megagon Labs,
Inc. (“Megagon”) license conditions and the OSS license conditions, the applicable OSS
conditions governing the corresponding OSS components shall prevail.
You agree not to, and are not permitted to, distribute actual datasets used with the OSS
components listed below. You agree and are limited to distribute only links to datasets from
known sources by listing them in the datasets overview table below. You agree that any right to
modify datasets originating from parties other than Megagon are governed by the respective
third party’s license conditions.
You agree that Megagon grants no license as to any of its intellectual property and patent rights.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS (INCLUDING
MEGAGON) “AS IS” AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE. You agree to cease using,
incorporating, and distributing any part of the provided materials if you do not agree with the
terms or the lack of any warranty herein.
While Megagon makes commercially reasonable efforts to ensure that citations in this
document are complete and accurate, errors may occur. If you see any error or omission, please
help us improve this document by sending information to contact_oss@megagon.ai.

### Datasets

All datasets used within the product are listed below (including their copyright holders and the license information).

For Datasets having different portions released under different licenses, please refer to the included source link specified for each of the respective datasets for identifications of dataset files released under the identified licenses.

</br>

| ID  | Dataset Name | Modified | Copyright Holder | Upstream Link | License  |
|-----|----------------------------------|----------|------------------|-----------------------------------------------------------------------------------------------------------|--------------------|
| 1 | WordNet 3.0 (the NLTK `wordnet` corpus — downloaded at runtime by `blue-data-wordnet-mcp`, not redistributed) | No | Princeton University | [link](https://wordnet.princeton.edu/) | [WordNet 3.0 License](https://wordnet.princeton.edu/license-and-commercial-use) |
| 2 | Open Multilingual Wordnet 1.4 (the NLTK `omw-1.4` corpus — downloaded at runtime by `blue-data-wordnet-mcp`, not redistributed) | No | Francis Bond et al. and the individual wordnet projects | [link](https://omwn.org/) | Various open licenses, one per language wordnet (each shipped as its `LICENSE` in the corpus) |

### Open Source Software (OSS) Components

All OSS components used within the product are listed below (including their copyright holders and the license information).

For OSS components having different portions released under different licenses, please refer to the included Upstream link(s) specified for each of the respective OSS components for identifications of code files released under the identified licenses.

</br>

| ID  | OSS Component Name | Modified | Copyright Holder | Upstream Link | License  |
|-----|----------------------------------|----------|------------------|-----------------------------------------------------------------------------------------------------------|--------------------|
| 1 | arrow | No | Chris Smith | [link](https://github.com/arrow-py/arrow) | Apache Software License |
| 2 | chromadb | No | Chroma | [link](https://github.com/chroma-core/chroma) | Apache Software License |
| 3 | click | No | Pallets | [link](https://github.com/pallets/click) | BSD License |
| 4 | langchain-mcp-adapters | No | LangChain, Inc. | [link](https://github.com/langchain-ai/langchain-mcp-adapters) | MIT License |
| 5 | langchain-openai | No | LangChain, Inc. | [link](https://github.com/langchain-ai/langchain) | MIT License |
| 6 | langgraph | No | LangChain, Inc. | [link](https://github.com/langchain-ai/langgraph) | MIT License |
| 7 | litellm | No | Berri AI | [link](https://github.com/BerriAI/litellm) | MIT License |
| 8 | MCP | No | Anthropic, PBC | [link](https://github.com/modelcontextprotocol/python-sdk) | MIT License |
| 9 | neo4j | No | Neo4j, Inc. | [link](https://github.com/neo4j/neo4j-python-driver) | Apache Software License |
| 10 | nltk | No | NLTK Team | [link](https://github.com/nltk/nltk) | Apache Software License |
| 11 | openai | No | OpenAI | [link](https://github.com/openai/openai-python) | Apache Software License |
| 12 | pydantic | No | Pydantic Services Inc. and individual contributors | [link](https://github.com/pydantic/pydantic) | MIT License |
| 13 | rank-bm25 | No | Dorian Brown | [link](https://github.com/dorianbrown/rank_bm25) | Apache Software License |
| 14 | redis | No | Redis, inc. | [link](https://github.com/redis/redis-py) | MIT License |
| 15 | rich | No | Will McGugan | [link](https://github.com/Textualize/rich) | MIT License |
