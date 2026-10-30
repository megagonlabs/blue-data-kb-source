# blue-data-kb-cli

Command-line interface for the Blue Data Knowledge-Base. A thin adapter over the `blue-data-kb-source` Python library built with Click and Rich.

## Requirements

- Python 3.12 or 3.13
- [`blue-data`](https://github.com/megagon-internal/blue-data/tree/1.0/lib) (local dependency)
- [`blue-data-kb-source`](../lib) (local dependency)

## Installation

```bash
cd platform/cli
bash scripts/install.sh
```

Or manually:

```bash
pip install -e ../../../blue-data/lib
pip install -e ../../lib
pip install -e .
```

For LLM features (`answer`, `validate`, `extract`, `schema expand`), also install the `llm` extra on the library — LiteLLM, which sends the calls to the `BLUE_DATA_LLM_PROVIDER` provider (`openai` is the extra's former name and still works):

```bash
pip install -e "../../lib[llm]"
```

## State persistence

Each database is registered in a registry file:

```
~/.blue-kb/registry.json
```

For the default `file` graph backend, data is automatically persisted as a JSON file — no explicit save step is needed:

```
~/.blue-kb/{database}.json
```

`FileGraphSource` saves after every write operation, so IDs returned by one command can be safely passed to the next. Override the data directory:

```bash
export BLUE_DATA_KB_SOURCE_DATA_DIR=/path/to/my-kb   # or: blue-kb profile config BLUE_DATA_KB_SOURCE_DATA_DIR /path/to/my-kb
```

## Registering a database

Use `blue-kb database register` to create and configure a database:

```bash
# File backend, BM25 keyword search (index persisted to disk)
blue-kb database register main --graph-backend file --search bm25

# File backend, semantic (vector) search with OpenAI embeddings. Vectors are stored
# as files beside the graph (`--vector-directory` to put them elsewhere).
blue-kb database register main --graph-backend file --search semantic

# The same with a ChromaDB vector store, at <graph directory>/chromadb unless
# --vector-path says otherwise. Install the driver first: `pip install chromadb`
# (or the CLI's `chromadb` extra). `--vector-backend memory` is refused: every
# blue-kb command is a new process, so in-memory vectors would never be found.
blue-kb database register main \
    --graph-backend file \
    --search semantic \
    --vector-backend chromadb \
    --embedding-provider openai \
    --embedding-model text-embedding-3-small

# Neo4j backend with Redis vector store. Install both drivers first (or the CLI's
# `neo4j` and `redis` extras). The password comes from the profile (it prompts, so
# it stays out of shell history), never from a flag or the registry.
pip install neo4j redis
blue-kb profile config BLUE_DATA_KB_SOURCE_NEO4J_PASSWORD
blue-kb database register main \
    --graph-backend neo4j \
    --neo4j-uri bolt://localhost:7687 \
    --neo4j-user neo4j \
    --search semantic \
    --vector-backend redis \
    --redis-host localhost \
    --redis-port 6379
# Redis keys and indexes are namespaced by --redis-scan-prefix (default `blue_kb:`);
# set a different one to keep two KB installations on one Redis server apart.
# If a vector index can't be created, the write that triggered it fails with the
# cause. The graph write itself is already committed, so after fixing the vector
# config run `blue-kb data reindex` to index what was written in the meantime.

# Update search configuration for an existing database
blue-kb --database main database search config set --search bm25

# View current configuration
blue-kb --database main database config show
blue-kb --database main database search config show

# Rebuild the search index after bulk imports or enabling search on an existing database
blue-kb --database main data reindex
```

`--neo4j-uri` takes any scheme the Neo4j driver accepts — `bolt`, `neo4j`, and their TLS forms `bolt+s`/`neo4j+s` (`+ssc` accepts a self-signed certificate) — and connects with it as given; any other scheme is an error. The Neo4j password is never a command-line option or part of the registry: every command reads it from the profile's `BLUE_DATA_KB_SOURCE_NEO4J_PASSWORD` (or that environment variable) when it connects, and `database list`/`config show` mask any credential an older registry record still holds.

## Global options

```
blue-kb [--profile NAME] [--database DB] COMMAND
```

`--profile` selects the configuration profile; `--database` selects the target database.

`--database` defaults to `~/.blue-kb/.selected.database` (written by `blue-kb database select`); if that file does not exist, the profile's `BLUE_DATA_KB_SOURCE_DATABASE` is used, then the database named `"default"`. Precedence is `--database` > `blue-kb database select` > profile > `"default"`.

## Profiles and configuration

A **profile** is a named set of configuration values — API keys, models, directories — stored as one section of `~/.blue-kb/.profiles`. Everything the CLI reads comes from the selected profile, so switching environments is one command rather than a shell full of exports.

The first `blue-kb` command creates a `default` profile and selects it, so there is nothing to set up before the CLI works:

```bash
$ blue-kb profile config                     # walk through the required parameters
$ blue-kb profile inspect                    # every value, and where it came from
```

`add` is for additional profiles:

```bash
$ blue-kb profile add staging
$ blue-kb profile select staging
$ blue-kb profile config                     # configures staging, now that it is selected
```

The `default` profile is created empty, so any value you never set keeps tracking the built-in default rather than being frozen at whatever it was when you installed. Removing a profile sticks — it is not recreated on the next command.

### Setting values

`blue-kb profile config` has three forms:

```bash
blue-kb profile config                                  # prompt for every required parameter
blue-kb profile config BLUE_DATA_LLM_MODEL              # prompt for one
blue-kb profile config BLUE_DATA_LLM_MODEL gpt-4o       # set one directly
```

Add `--profile NAME` to configure a profile other than the selected one.

### Precedence

**command option > environment variable > profile > default**

`blue-kb profile inspect` reports the winning tier for every parameter, so a surprising value can be traced to whatever set it.

### Secrets

Secrets are never accepted as a command-line value — that would put them in your shell history — so `blue-kb profile config OPENAI_API_KEY` always prompts. Two forms are accepted:

```bash
$ blue-kb profile config OPENAI_API_KEY
  value: sk-svcacct-...        # stored encoded in ~/.blue-kb/.profiles
$ blue-kb profile config OPENAI_API_KEY
  value: $OPENAI_API_KEY       # stored as a reference; read from the environment at use
```

`~/.blue-kb/.profiles` is written `0600`, and `blue-kb profile inspect` masks stored secrets. A reference is shown by name with whether it currently resolves (`$OPENAI_API_KEY (set)`), which tells you what to export without revealing anything.

> The encoded form is obfuscation, not encryption — it keeps a key off your screen and out of a careless `grep`, and anyone who can read the file can decode it. The file mode is the real protection. Where that is not enough, use the `$VAR` reference form so the secret never touches disk.

### Parameters

| Parameter | Cast | Default | Asked by `config` |
|---|---|---|---|
| `OPENAI_API_KEY` | secret | — | ✓ |
| `ANTHROPIC_API_KEY` | secret | — | |
| `BLUE_DATA_LLM_PROVIDER` | str | blank (`openai`) | |
| `BLUE_DATA_LLM_MODEL` | str | unset (the provider's default) | |
| `BLUE_DATA_CLI_FORMAT` | str | `table` | |
| `BLUE_DATA_CLI_LOG_LIMIT` | int | `80` | |
| `BLUE_DATA_KB_SOURCE_DATA_DIR` | directory | `~/.blue-kb` | ✓ |
| `BLUE_DATA_KB_SOURCE_DATABASE` | str | `default` | ✓ |
| `BLUE_DATA_KB_SOURCE_NEO4J_PASSWORD` | secret | — | |
| `BLUE_DATA_KB_SOURCE_LLM_MODEL` | str | *inherits* | |
| `BLUE_DATA_KB_SOURCE_EXTRACTION_LLM_MODEL` | str | *inherits* | |
| `BLUE_DATA_KB_SOURCE_QUERY_LLM_MODEL` | str | *inherits* | |
| `BLUE_DATA_KB_SOURCE_VALIDATION_LLM_MODEL` | str | *inherits* | |
| `BLUE_DATA_KB_SOURCE_SCHEMA_EXPANSION_LLM_MODEL` | str | *inherits* | |
| `BLUE_DATA_KB_SOURCE_ENRICHMENT_LLM_MODEL` | str | *inherits* | |
| `BLUE_DATA_KB_SOURCE_INGESTION_LLM_MODEL` | str | *inherits* | |
| `BLUE_DATA_KB_SOURCE_ASSERTION_LLM_MODEL` | str | *inherits* | |
| `BLUE_DATA_KB_SOURCE_SCHEMA_EXPANSION_TIMEOUT` | float | `1800` | |
| `BLUE_DATA_KB_SOURCE_EXTRACTION_EFFORT` | str | `low` | |
| `BLUE_DATA_KB_SOURCE_EXTRACTION_MAX_CONCURRENCY` | int | `1` | |
| `BLUE_DATA_KB_SOURCE_EXTRACTION_MAX_TURNS` | int | `25` | |
| `BLUE_DATA_KB_SOURCE_EXTRACTION_RELATED_DATA_K` | int | `10` | |
| `BLUE_DATA_KB_SOURCE_EXTRACTION_EXPAND_SCHEMA` | bool | `true` | |

Each per-function model falls back to `BLUE_DATA_KB_SOURCE_LLM_MODEL`, then to `BLUE_DATA_LLM_MODEL` — so setting one value covers every command, and a single function can still be pinned to a different model. When none of them is set, the LLM commands use the provider's default — `gpt-4o` for `openai`, `claude-opus-4-8` for `anthropic` — and `profile inspect` shows that default, with `openai default` or `anthropic default` as its source.

**Provider.** Every LLM call goes through [LiteLLM](https://docs.litellm.ai) to the provider `BLUE_DATA_LLM_PROVIDER` names; blank means `openai`, as in blue-data-ops. The models are that provider's: with `BLUE_DATA_LLM_PROVIDER=anthropic`, set `ANTHROPIC_API_KEY` and use Claude model names (`claude-opus-4-8`, the default). Another LiteLLM provider (`gemini`, say) works when you name a model — kb has no default for it — and set the key variable LiteLLM reads for it. Embeddings for semantic search use OpenAI and `OPENAI_API_KEY` whatever the provider.

`BLUE_DATA_KB_SOURCE_HOME` (default `~/.blue-kb`) is deliberately *not* a profile parameter: it is the directory the profiles themselves live in, so it can only be set in the environment.

## Walkthrough

```bash
# 1. Register and select a database
$ blue-kb database list
$ blue-kb database register main --search bm25
Registered database: main (graph=file, search=bm25)

$ blue-kb database select main

# 2. Declare schema
$ blue-kb schema class add PERSON "A human individual"
Added class: PERSON (id: 3b2a906c-…)

$ blue-kb schema class property add AGE "Age of a person" --class PERSON --type number
Added class property: AGE (id: 0d9eaaac-bf07-41e6-bd6d-34f60f4c90d7)

$ blue-kb schema class add COMPANY "A business organization"
Added class: COMPANY (id: 49710e2e-…)

$ blue-kb schema relation add WORKED_AT "Employment relationship" \
    --domain PERSON --range COMPANY
Added relation: WORKED_AT (id: 67a75f53-…)

$ blue-kb schema relation property add START_DATE "Start of employment" \
    --relation WORKED_AT --type date
Added relation property: START_DATE (id: 8c3d0a1e-…)

# 3. Register a provenance source
$ blue-kb data source add resume_alice --name "Alice's résumé"
Added source: resume_alice (id: a1b2c3d4-…)

# 4. Add entity instances (class and source resolved by name)
$ blue-kb data entity add --name Alice --class PERSON \
    --text "Alice is a software engineer." --source "Alice's résumé"
Added entity: Alice (id: d52378c9-…)

$ blue-kb data entity add --name IBM --class COMPANY
Added entity: IBM (id: 5b1a47fb-…)

# 5. Set and list entity property values
$ blue-kb data entity property set 42 \
    --entity Alice --property AGE --type NUMBER
Set entity property (literal id: 1a2b3c4d-…)

$ blue-kb data entity property list --entity Alice
┏━━━━━━━━━━┳━━━━━━━┳━━━━━━━━┓
┃ Property ┃ Value ┃ Type   ┃
┡━━━━━━━━━━╇━━━━━━━╇━━━━━━━━┩
│ AGE      │ 42    │ NUMBER │
└──────────┴───────┴────────┘

# 6. Add a fact (head/tail/relation/source resolved by name or by ID)
$ blue-kb data fact add \
    --head Alice --relation WORKED_AT --tail IBM \
    --text "Alice joined IBM in June 1998." --source "Alice's résumé"
Added fact (id: 092733dd-…)

# 7. Set and list fact property values
$ blue-kb data fact property set "1998-06-01" \
    --fact-id 092733dd-… --property START_DATE --type DATE
Set fact property (literal id: 5e6f7a8b-…)

$ blue-kb data fact property list --fact-id 092733dd-…
┏━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━┓
┃ Property   ┃ Value      ┃ Type   ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━┩
│ START_DATE │ 1998-06-01 │ DATE   │
└────────────┴────────────┴────────┘

# 8. List the full schema
$ blue-kb schema list
                 Classes                  
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Name         ┃ Description             ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ COMPANY      │ A business organization │
│ PERSON       │ A human individual      │
└──────────────┴─────────────────────────┘
                     Class Properties                     
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
┃ Class        ┃ Property     ┃ Type   ┃ Description     ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│ PERSON       │ AGE          │ number │ Age of a person │
└──────────────┴──────────────┴────────┴─────────────────┘
                             Relations                              
┏━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Source     ┃ Relation     ┃ Target     ┃ Description             ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ PERSON     │ WORKED_AT    │ COMPANY    │ Employment relationship │
└────────────┴──────────────┴────────────┴─────────────────────────┘
                    Relation Properties                     
┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┓
┃ Relation     ┃ Property     ┃ Type ┃ Description         ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━━━━━━━━━━━┩
│ WORKED_AT    │ START_DATE   │ date │ Start of employment │
└──────────────┴──────────────┴──────┴─────────────────────┘
# 9. List all instances
$ blue-kb data list
# optionally with provenance:
# $ blue-kb data list --show-provenance
     Entities      
┏━━━━━━━┳━━━━━━━━━┓
┃ Name  ┃ Class   ┃
┡━━━━━━━╇━━━━━━━━━┩
│ IBM   │ COMPANY │
│ Alice │ PERSON  │
└───────┴─────────┘
   Entity Property Values    
┏━━━━━━━━┳━━━━━━━━━━┳━━━━━━━┓
┃ Entity ┃ Property ┃ Value ┃
┡━━━━━━━━╇━━━━━━━━━━╇━━━━━━━┩
│ Alice  │ AGE      │ 42    │
└────────┴──────────┴───────┘
           Facts            
┏━━━━━━━┳━━━━━━━━━━━┳━━━━━━┓
┃ Head  ┃ Relation  ┃ Tail ┃
┡━━━━━━━╇━━━━━━━━━━━╇━━━━━━┩
│ Alice │ WORKED_AT │ IBM  │
└───────┴───────────┴──────┘
                 Fact Property Values                 
┏━━━━━━━┳━━━━━━━━━━━┳━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Head  ┃ Relation  ┃ Tail ┃ Property   ┃ Value      ┃
┡━━━━━━━╇━━━━━━━━━━━╇━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ Alice │ WORKED_AT │ IBM  │ START_DATE │ 1998-06-01 │
└───────┴───────────┴──────┴────────────┴────────────┘
# 10. Search the schema
$ blue-kb schema search employment
┏━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Type       ┃ Name       ┃ Description             ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ RELATION   │ WORKED_AT  │ Employment relationship │
│ PROPERTY   │ START_DATE │ Start of employment     │
│ CLASS      │ PERSON     │ A human individual      │
│ PROPERTY   │ AGE        │ Age of a person         │
│ CLASS      │ COMPANY    │ A business organization │
└────────────┴────────────┴─────────────────────────┘
# Search schema restricted to relations only
$ blue-kb schema search employment --type relation

# 10b. Search data
$ blue-kb data search Alice
       Entities
┏━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ Name  ┃ Class    ┃ Description ┃
┡━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ Alice │ PERSON   │             │
└───────┴──────────┴─────────────┘

# Search facts only
$ blue-kb data search "IBM" --type fact

# 11. Answer a question
$ blue-kb data answer "Where did Alice work?"
┏━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Entity      ┃ Relation  ┃ Value   ┃ Provenance                     ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Alice       │           │         │                                │
│ Alice → IBM │ WORKED_AT │         │ Alice joined IBM in June 1998. │
└─────────────┴───────────┴─────────┴────────────────────────────────┘

# 12. Validate a statement
$ blue-kb data validate "Alice worked at IBM"

Supporting:
  • Alice —WORKED_AT→ IBM

Counter:
  (none)

# 13. LLM extraction (requires the llm extra and the provider's API key)
$ blue-kb data extract \
    "Alice later joined Google as a senior engineer in May 2002." \
    --query "What is her role?" --query "When did she start?"
Extraction complete.

# 14. Delete a database
$ blue-kb database delete main --yes
Deleted database: main
```

## Command reference

Shared `ASSERT_OPTS` on every `assert` command and `blue-kb data ingest`: `[--description D] [--source NAME|--source-id ID] [--confidence F] [--extraction-type text_span|external_record|assertion]` (default `assertion`) `[--schema-creation-mode llm|none]` (default `llm`; `llm` needs `OPENAI_API_KEY`) `[--model M]`. Ambiguous names fail with the candidates listed in the error message; nothing is written on failure.


| Command | Description |
|---|---|
| `blue-kb profile add NAME` | Create an additional profile (`default` is created on first use) |
| `blue-kb profile remove NAME [--confirm]` | Delete a profile and its configuration |
| `blue-kb profile list [--format table\|json]` | List profiles, marking the selected one |
| `blue-kb profile select NAME` | Set the default profile |
| `blue-kb profile inspect [NAME] [--format table\|json]` | Show every configuration value and the tier it came from; secrets masked |
| `blue-kb profile config [PARAM [VALUE]] [--profile NAME]` | Set configuration: no args walks the required parameters, PARAM prompts for one, PARAM VALUE sets it directly. Secrets always prompt |
| `blue-kb database list [--format table\|json]` | List registered databases with backend and search mode |
| `blue-kb database register NAME [options]` | Register a new database with graph and search backends. If the backend fails to initialize (missing driver, unreachable server), the registry is left as it was. Redis vector options: `--redis-host`, `--redis-port`, `--redis-db`, `--redis-scan-prefix` (key/index namespace, default `blue_kb:`) |
| `blue-kb database create NAME` | Create a database, inheriting the session database's backend and search config |
| `blue-kb database delete NAME [--yes]` | Delete a database and unregister it |
| `blue-kb database select NAME` | Set the default target database. A name that is neither registered nor an existing database is accepted with a warning: commands create a file-backed database of that name on first use |
| `blue-kb database config show` | Print graph backend config for the current database |
| `blue-kb database config set [--graph-backend …] [--directory …] [--neo4j-uri …] …` | Update graph backend config |
| `blue-kb database search config show` | Print search config for the current database |
| `blue-kb database search config set [--search none\|bm25\|semantic] [--bm25-index-dir …] [--vector-backend …] …` | Update search config. A vector option replaces the whole vector config; `--redis-*` options alone imply `--vector-backend redis` |
| `blue-kb schema list [--format table\|json]` | List classes, relations, properties, and hierarchy |
| `blue-kb schema export [-o\|--output FILE]` | Export the schema as JSON (default: stdout); same shape `blue-kb schema import` reads |
| `blue-kb schema import FILE` | Import a schema previously produced by `blue-kb schema export` (declares idempotently; `-` reads stdin) |
| `blue-kb schema class add NAME DESC` | Declare a CLASS (idempotent) |
| `blue-kb schema class assert NAME [ASSERT_OPTS]` | Assert a class: reuse when one resolves (exact, normalized, or search match), create otherwise |
| `blue-kb schema class list [--format table\|json]` | List classes |
| `blue-kb schema class update --class NAME\|--class-id ID [--name N] [--description D]` | Update the name and/or description of a class |
| `blue-kb schema class parent add [--class NAME\|--class-id ID] [--parent NAME\|--parent-id ID]` | Wire IS_SUBCLASS_OF from child to parent |
| `blue-kb schema class parent assert CHILD PARENT [ASSERT_OPTS]` | Add a subclass link, creating missing classes |
| `blue-kb schema class parent remove [--class NAME\|--class-id ID] [--parent NAME\|--parent-id ID]` | Remove IS_SUBCLASS_OF edge |
| `blue-kb schema class parent list [--class NAME\|--class-id ID] [--recursive] [--format table\|json\|tree]` | List superclasses |
| `blue-kb schema class child list [--class NAME\|--class-id ID] [--recursive] [--format table\|json\|tree]` | List subclasses |
| `blue-kb schema class property add NAME DESC [--class NAME\|--class-id ID] [--type NUMBER\|DATE\|BOOLEAN\|STRING]` | Declare a property on a class; `--type` is the type of value it holds, and a value that doesn't fit is refused |
| `blue-kb schema class property attach [--property NAME\|--property-id ID] [--class NAME\|--class-id ID]` | Attach an existing property to a class (idempotent) — reuse it instead of declaring a same-meaning duplicate |
| `blue-kb schema class property assert NAME --class NAME [ASSERT_OPTS]` | Add a class property, creating the class if missing |
| `blue-kb schema class property list [--class NAME\|--class-id ID] [--format table\|json]` | List class properties (optionally filtered by class) |
| `blue-kb schema class property update --property NAME\|--property-id ID [--class NAME\|--class-id ID] [--name N] [--description D] [--type T]` | Update the name and/or description of a class property; --class disambiguates when the name is shared |
| `blue-kb schema class property delete --property NAME\|--property-id ID [--class NAME\|--class-id ID] [--yes]` | Delete a class property (errors if values exist); --class disambiguates |
| `blue-kb schema class delete --class NAME\|--class-id ID [--yes]` | Delete a class (errors if instances or subclasses exist) |
| `blue-kb schema relation add NAME DESC [--domain C] [--range C]` | Declare a RELATION (idempotent) |
| `blue-kb schema relation assert NAME --domain C... --range C... [--is-symmetric] [ASSERT_OPTS]` | Assert a relation type; missing domain/range classes are created, an existing relation is widened instead of rejected |
| `blue-kb schema relation list [--format table\|json]` | List relations |
| `blue-kb schema relation update --relation NAME\|--relation-id ID [--name N] [--description D]` | Update the name and/or description of a relation |
| `blue-kb schema relation parent add [--relation NAME\|--relation-id ID] [--parent NAME\|--parent-id ID]` | Wire IS_SUBCLASS_OF from child to parent relation |
| `blue-kb schema relation parent assert CHILD PARENT [ASSERT_OPTS]` | Assert the same for relations; both must already exist |
| `blue-kb schema relation parent remove [--relation NAME\|--relation-id ID] [--parent NAME\|--parent-id ID]` | Remove IS_SUBCLASS_OF edge between relations |
| `blue-kb schema relation parent list [--relation NAME\|--relation-id ID] [--recursive] [--format table\|json\|tree]` | List parent relations |
| `blue-kb schema relation child list [--relation NAME\|--relation-id ID] [--recursive] [--format table\|json\|tree]` | List sub-relations |
| `blue-kb schema relation domain attach [--relation NAME\|--relation-id ID] [--class NAME\|--class-id ID]` | Attach a class to the relation's domain (deduplicated; add-only by design — no detach counterpart) |
| `blue-kb schema relation domain list [--relation NAME\|--relation-id ID] [--format table\|json]` | List a relation's domain classes |
| `blue-kb schema relation range attach [--relation NAME\|--relation-id ID] [--class NAME\|--class-id ID]` | Attach a class to the relation's range (deduplicated; add-only by design — no detach counterpart) |
| `blue-kb schema relation range list [--relation NAME\|--relation-id ID] [--format table\|json]` | List a relation's range classes |
| `blue-kb schema relation property add NAME DESC [--relation NAME\|--relation-id ID] [--type NUMBER\|DATE\|BOOLEAN\|STRING]` | Declare a property on a relation; `--type` is the type of value it holds, and a value that doesn't fit is refused |
| `blue-kb schema relation property attach [--property NAME\|--property-id ID] [--relation NAME\|--relation-id ID]` | Attach an existing property to a relation (idempotent) — reuse it instead of declaring a same-meaning duplicate |
| `blue-kb schema relation property assert NAME --relation NAME [ASSERT_OPTS]` | Add a relation property (relation must exist) |
| `blue-kb schema relation property list [--relation NAME\|--relation-id ID] [--format table\|json]` | List relation properties (optionally filtered by relation) |
| `blue-kb schema relation property update --property NAME\|--property-id ID [--relation NAME\|--relation-id ID] [--name N] [--description D] [--type T]` | Update the name and/or description of a relation property; --relation disambiguates when the name is shared |
| `blue-kb schema relation property delete --property NAME\|--property-id ID [--relation NAME\|--relation-id ID] [--yes]` | Delete a relation property (errors if values exist); --relation disambiguates |
| `blue-kb schema relation delete --relation NAME\|--relation-id ID [--yes]` | Delete a relation (errors if facts or sub-relations exist) |
| `blue-kb schema search QUERY [--type class\|relation\|property]… [--mode keyword\|semantic] [--top-k N] [--format table\|json]` | Search schema nodes; `--mode semantic` requires database configured with `--search semantic`; `--type` is repeatable |
| `blue-kb schema expand TEXT [--depth N] [--reference llm\|mcp] [--mcp-server JSON]… [--dedup-mode search\|llm\|none] [--model M] [--timeout SECONDS] [--verbose\|-v] [--log\|-l] [--log-limit N]` | LLM schema expansion, grounded by TEXT — declares CLASS/RELATION/PROPERTY only, never entities/facts. `--depth` (default `1`) — hierarchy levels beyond what's directly evidenced to also propose, linked in via `add_subclass_of`, each link first checked by an LLM plausibility judge; each new property is likewise checked against its owner — rejected if it doesn't describe it, reused if the owner already has a same-meaning one (never as a declared RELATION — taxonomic names like IS_A/TYPE_OF/SUBCLASS_OF are rejected). `--reference mcp` additionally gives the model tools from `--mcp-server` (repeatable JSON, e.g. `'{"transport":"stdio","command":"blue-data-wordnet-mcp"}'`) to ground names/hierarchy; lookups are required in that mode (a new class or relation declared without one is sent back once for it). `--dedup-mode` behaves as `blue-kb data extract`'s `--schema-expansion-dedup-mode` below, and is also auto-applied before every new CLASS/RELATION is created, even if the model skips search_schema. That automatic check reuses an existing element only on strong evidence — the `llm` judge's verdict, or a semantic search hit at similarity ≥ 0.9 (keyword/BM25 overlap alone never merges) — and, for a relation, only when its domain/range share a class lineage with the requested ones; a redirected declaration's tool result names the reused element (`reused_existing`). `--timeout` (default: profile `BLUE_DATA_KB_SOURCE_SCHEMA_EXPANSION_TIMEOUT`, `1800`; `0` = no limit) caps the whole run and fails with a clear error on expiry, keeping what was declared so far — reasoning models such as gpt-5 take ~30–60 s per turn. Run once, then `blue-kb data extract --no-expand-schema` to extract many documents against the resulting schema. Requires the `openai` extra (and `mcp` for `--reference mcp`) |
| `blue-kb data list [--show-provenance] [--show-detailed] [--format table\|json]` | List all entity, property-value, and fact instances; `--show-provenance` expands each row into one row per piece of evidence, with its own score, text, and source; `--show-detailed` adds each row's description/created/modified, and confidence on fact rows (table format shows created/modified as human-readable local dates, e.g. "Aug 7, 2026 5:48 PM"; `--format json` keeps the raw ISO-8601 timestamps); combining both flags additionally shows the linked source's `original` attribute (if any) after the source name |
| `blue-kb data export [--show-provenance] [-o\|--output FILE]` | Export instance data (entities, facts, property values) as JSON (default: stdout); same shape `blue-kb data import` reads. Does not include schema — export/import that first via `blue-kb schema export`/`blue-kb schema import` |
| `blue-kb data import FILE` | Import data previously produced by `blue-kb data export`; referenced classes/relations/properties must already exist (`-` reads stdin) |
| `blue-kb data ingest FILE [--format table\|json] [ASSERT_OPTS minus --description]` | Bulk-ingest a ten-arm JSON payload (`-` reads stdin, same ergonomics as `blue-kb data import`); rows run through the per-item asserts in file order, failures collect into the report (a Failures table, or the full report as JSON) |
| `blue-kb data source add SOURCE [--name N] [--description D] [--original DATE]` | Register a provenance source; `--original` records the source's original date (e.g. publication date), separate from `created`. Every `--source` option elsewhere accepts either this `SOURCE` key or the `--name` label (name is tried first); an unknown value lists the known sources |
| `blue-kb data source list [--show-detailed] [--format table\|json]` | List all registered provenance sources; `--show-detailed` adds Created/Modified columns to the table as human-readable local dates (`--format json` always includes `original`/`created`/`modified` as raw ISO-8601 timestamps) |
| `blue-kb data source update --source NAME\|--source-id ID [--name N] [--description D] [--original DATE]` | Update the name, description, and/or original date of a source |
| `blue-kb data source delete --source NAME\|--source-id ID [--yes]` | Delete a source and its extraction records; instances are preserved |
| `blue-kb data entity add [--name NAME] [--class NAME\|--class-id ID] [--description D] [--text T] [--source NAME\|--source-id ID] [--confidence F] [--extraction-type T]` | Add an entity |
| `blue-kb data entity assert NAME [--class NAME] [ASSERT_OPTS]` | Assert an entity by name: reuse it when one resolves, else create it with a judged class (`EVERYTHING` under `--schema-creation-mode none`); output shows the outcome (created/reused/deduped) and any cascade-created nodes |
| `blue-kb data entity list [--show-provenance] [--format table\|json]` | List all entity instances; `--show-provenance` expands each row into one row per piece of evidence, with its own score, text, and source |
| `blue-kb data entity update --entity NAME\|--entity-id ID [--name N] [--description D]` | Update the name and/or description of an entity |
| `blue-kb data entity delete --entity NAME\|--entity-id ID [--cascade] [--yes]` | Delete an entity and its property values; with --cascade also deletes referencing facts |
| `blue-kb data entity class attach [--entity NAME\|--entity-id ID] [--class NAME\|--class-id ID]` | Attach a class to an existing entity (deduplicated; add-only by design — no detach counterpart) |
| `blue-kb data entity class list [--entity NAME\|--entity-id ID] [--format table\|json]` | List an entity's classes |
| `blue-kb data entity extraction add --entity NAME\|--entity-id ID --text T [--source NAME\|--source-id ID] [--confidence F] [--extraction-type T]` | Link additional source text (a new `EXTRACTED_FROM` edge) to an existing entity |
| `blue-kb data entity property set VALUE [--name N] [--description D] --entity NAME\|--entity-id ID --property NAME [--type T] [--source NAME\|--source-id ID] [--confidence F] [--extraction-type T]` | Set an entity property value |
| `blue-kb data entity property assert --entity E --property P --value V [--type T] [ASSERT_OPTS]` | Assert a property value on an entity; equal values dedupe, property placement is judged |
| `blue-kb data entity property list --entity NAME\|--entity-id ID [--show-provenance] [--format table\|json]` | List property values for an entity; `--show-provenance` expands each row into one row per piece of evidence, with its own score, text, and source |
| `blue-kb data entity property update --entity NAME\|--entity-id ID --property NAME\|--property-id ID [--value V] [--name N] [--description D]` | Update the value, name, and/or description of an entity property value |
| `blue-kb data entity property delete --entity NAME\|--entity-id ID --property NAME\|--property-id ID [--yes]` | Delete a property value from an entity |
| `blue-kb data entity property extraction add --entity NAME\|--entity-id ID --property NAME\|--property-id ID --text T [--source NAME\|--source-id ID] [--confidence F] [--extraction-type T]` | Link additional source text to an entity's existing property value |
| `blue-kb data fact add [--name N] [--description D] [--head NAME\|--head-id ID] [--relation NAME\|--relation-id ID] [--tail NAME\|--tail-id ID] [--source NAME\|--source-id ID] [--confidence F] [--extraction-type T]` | Add a fact |
| `blue-kb data fact assert --head H --relation R --tail T [--name N] [ASSERT_OPTS]` | Assert a fact by names; operands are resolved or created, an existing triple dedupes with new evidence, non-conforming domain/range widens |
| `blue-kb data fact list [--show-provenance] [--format table\|json]` | List all facts with each fact's derived confidence; `--show-provenance` expands each fact into one row per piece of evidence, with its own score, text, and source |
| `blue-kb data fact update --fact NAME\|--fact-id ID [--name N] [--description D]` | Update the name and/or description of a fact |
| `blue-kb data fact delete --fact NAME\|--fact-id ID [--yes]` | Delete a fact and its property values |
| `blue-kb data fact extraction add --fact NAME\|--fact-id ID --text T [--source NAME\|--source-id ID] [--confidence F] [--extraction-type T]` | Link additional source text (a new `EXTRACTED_FROM` edge) to an existing fact |
| `blue-kb data fact property set VALUE [--name N] [--description D] --fact NAME\|--fact-id ID --property NAME [--type T] [--source NAME\|--source-id ID] [--confidence F] [--extraction-type T]` | Set a fact property value |
| `blue-kb data fact property assert --head H --relation R --tail T --property P --value V [--type T] [ASSERT_OPTS]` | Assert a property value on the fact identified structurally by its triple; a missing fact is created |
| `blue-kb data fact property list --fact NAME\|--fact-id ID [--show-provenance] [--format table\|json]` | List property values for a fact; `--show-provenance` expands each row into one row per piece of evidence, with its own score, text, and source |
| `blue-kb data fact property update --fact NAME\|--fact-id ID --property NAME\|--property-id ID [--value V] [--name N] [--description D]` | Update the value, name, and/or description of a fact property value |
| `blue-kb data fact property delete --fact NAME\|--fact-id ID --property NAME\|--property-id ID [--yes]` | Delete a property value from a fact |
| `blue-kb data fact property extraction add --fact NAME\|--fact-id ID --property NAME\|--property-id ID --text T [--source NAME\|--source-id ID] [--confidence F] [--extraction-type T]` | Link additional source text to a fact's existing property value |
| `blue-kb data reindex` | Rebuild the BM25/vector search index for the current database |
| `blue-kb data search QUERY [--type entity\|fact]… [--top-k N] [--format table\|json]` | Keyword or semantic search over entities and facts; search mode depends on database config; `--type` is repeatable |
| `blue-kb data answer QUESTION [--format table\|json\|nl] [--model M] [--include-metadata] [--verbose\|-v] [--log\|-l] [--log-limit N]` | LLM answer query; `--format nl` returns a prose answer; `--include-metadata` also collects created/modified, confidence, and per-extraction evidence for cited entities/facts/properties — shown as a second "Metadata" table (`--format table`), a `metadata` key alongside `result` (`--format json`), or folded into the prose as a follow-up section (`--format nl`); `--verbose` shows tool call arguments in the status line; `--log` also prints each tool call on its own persistent line; `--log-limit` sets the truncation length for verbose/log output, defaulting to the profile's `BLUE_DATA_CLI_LOG_LIMIT` |
| `blue-kb data validate STATEMENT [--natural-language] [--model M] [--include-metadata] [--verbose\|-v] [--log\|-l] [--log-limit N]` | LLM supporting / counter evidence — only facts and property values that exist in the KB, printed as `head —RELATION→ tail` and `owner · PROPERTY = value`; anything else the agent cited is listed under "Unverified"; `--natural-language` returns a prose assessment; `--include-metadata` behaves as in `blue-kb data answer` (a "Metadata" table normally, folded into the prose with `--natural-language`); `--verbose` shows tool call arguments in the status line; `--log` also prints each tool call on its own persistent line; `--log-limit` sets the truncation length for verbose/log output, defaulting to the profile's `BLUE_DATA_CLI_LOG_LIMIT` |
| `blue-kb data extract TEXT [--query Q]… [--source NAME\|--source-id ID] [--model M] [--effort low\|medium\|high] [--max-concurrency N] [--max-turns N] [--related-data-k N] [--expand-schema\|--no-expand-schema] [--schema-expansion-target text\|queries]… [--schema-expansion-depth N] [--schema-expansion-mode llm\|mcp] [--schema-expansion-mcp-server JSON]… [--schema-expansion-dedup-mode search\|llm\|none] [--schema-expansion-timeout SECONDS] [--verbose\|-v] [--log\|-l] [--log-limit N]` | LLM extraction against the schema as it currently stands (never declares new schema — a mention with no matching element is skipped). `--expand-schema` (default: on) runs `blue-kb schema expand` first, grounded by `--schema-expansion-target` (any combination of `text`/`queries`, default both); `--schema-expansion-depth`/`--schema-expansion-mode`/`--schema-expansion-mcp-server`/`--schema-expansion-dedup-mode` mirror `blue-kb schema expand`'s `--depth`/`--reference`/`--mcp-server`/`--dedup-mode`. Use `--no-expand-schema` to only extract, e.g. against a schema already expanded once via `blue-kb schema expand`. `--verbose` shows tool call arguments in the status line; `--log` also prints each tool call on its own persistent line; `--log-limit` sets the truncation length for verbose/log output. `--model`, `--effort`, `--max-concurrency`, `--max-turns`, `--related-data-k`, `--expand-schema/--no-expand-schema` and `--log-limit` each default to the selected profile's corresponding parameter — see [Profiles and configuration](#profiles-and-configuration) |

Exit codes: `0` success · `1` any error · `2` bad arguments. Errors print as one line, `Error: …`, naming the setting to fix where the cause is known (a missing or rejected provider API key — `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` — an unreachable or refusing Neo4j or Redis server); set `BLUE_DATA_KB_SOURCE_TRACEBACK=1` to get the full Python traceback instead.

## Project structure

```
platform/cli/
├── pyproject.toml
├── scripts/
│   ├── install.sh   # installs blue-data, blue-data-kb-source, then this package
│   ├── build.sh     # python -m build
│   └── deploy.sh    # twine upload dist/*
├── src/
│   └── blue_data_kb_cli/
│       ├── __init__.py
│       ├── config.py     # Registry, _make_kb factory, vector/embedding factories
│       └── commands.py   # Click command groups
└── tests/
    └── unit/
```

## Running tests

```bash
cd platform/cli
python3 -m pytest tests/ -v
```

Every test runs the real Click commands through `CliRunner` against an isolated home directory
(a per-test `tmp_path` stands in for `~/.blue-kb`), so nothing touches your own databases or
profiles. No network or API key is needed: the LLM commands (`data extract`,
`data answer`, `data validate`, `schema expand`, …) are tested with `blue_data_kb_source.llm.AsyncClient` mocked.
