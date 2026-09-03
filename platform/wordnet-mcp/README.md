# blue-data-wordnet-mcp

MCP server that exposes [WordNet](https://wordnet.princeton.edu/) lexical
lookups (via `nltk.corpus.wordnet`) as tools for LLM clients (Claude Desktop,
Claude Code, and any MCP-compatible host).

Read-only — WordNet is a fixed, pre-built lexical database, so there are no
write tools.

Wired into `blue-data-kb-source`'s schema expansion: `kb schema expand
--reference mcp` and `kb data extract --schema-expansion-mode mcp` both spawn
this server over stdio (via `--mcp-server` / `--schema-expansion-mcp-server`)
and give the model its tools to ground class/relation names and hierarchy
placement before declaring them. See "Schema expansion integration" below.

---

## Installation

```bash
cd platform/wordnet-mcp
pip install -e .
```

The WordNet corpus is not bundled — it's downloaded automatically (via
`nltk.download`) the first time any tool runs, if not already present
locally. This needs network access once; after that it's cached and the
server works offline. To pre-download instead of relying on the lazy
download:

```bash
python -m nltk.downloader wordnet omw-1.4
```

---

## Running

```bash
blue-data-wordnet-mcp
```

| Variable | Default | Description |
|---|---|---|
| `BLUE_DATA_WORDNET_MCP_NLTK_DATA_DIR` | nltk's default (`~/nltk_data`) | Where the WordNet corpus is downloaded to / looked up from. |

---

## Claude Desktop integration

Add to `~/.config/claude-desktop/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "wordnet": {
      "command": "blue-data-wordnet-mcp"
    }
  }
}
```

If `blue-data-wordnet-mcp` is not on the system PATH, use the full path to
the venv's binary.

---

## Schema expansion integration

No need to run the server yourself first — `kb schema expand` /
`kb data extract` spawn it as a subprocess over stdio for the duration of the
call, given its command via `--mcp-server` / `--schema-expansion-mcp-server`.

```bash
kb schema expand "Dogs are a type of canine, and canines are carnivores." \
  --reference mcp \
  --mcp-server '{"transport":"stdio","command":"blue-data-wordnet-mcp"}'
```

```bash
kb data extract "..." \
  --schema-expansion-mode mcp \
  --schema-expansion-mcp-server '{"transport":"stdio","command":"blue-data-wordnet-mcp"}'
```

Add `-v` (or `-l` for one line per tool call) to watch the WordNet lookups
(`lookup_synsets`, `get_hypernyms`, ...) the model makes before it declares
classes and relations.

---

## Tool reference

WordNet relations (hypernyms, meronyms, synonyms, antonyms, similarity) are
defined on *synsets* (word senses), not raw words — a word like "bank" has
several unrelated synsets. The typical flow is: resolve a word to a synset id
with `lookup_synsets`, then pass that id to the other tools. Synset ids use
WordNet's own naming (`lemma.pos.sense_number`), e.g. `dog.n.01`.

### `lookup_synsets`

| Parameter | Type | Description |
|---|---|---|
| `word` | `str` | Word to look up, e.g. `bank`, `run` |
| `pos` | `str \| None` | Restrict to one part of speech: `n`/`v`/`a`/`r`/`s`. Omit to search all |

Returns a list of `{id, pos, definition, examples, lemma_names}` — one per
candidate sense.

### `get_synset`

`synset_id` (e.g. `dog.n.01`) → full detail for that one synset, same shape
as one `lookup_synsets` entry.

### `get_synonyms`

`synset_id` → `{id, synonyms}` — the lemma names sharing that exact sense.

### `get_hypernyms` / `get_hyponyms`

| Parameter | Type | Description |
|---|---|---|
| `synset_id` | `str` | e.g. `dog.n.01` |
| `depth` | `int` | How many levels to walk (default `1` = immediate parents/children) |

Walks the is-a hierarchy up (`get_hypernyms`, toward more general senses —
dog → canine → carnivore) or down (`get_hyponyms`, toward more specific
senses — dog → puppy, poodle). Returns a list of synset entries, each tagged
with `distance` from `synset_id`. A synset reachable via multiple paths keeps
its shortest distance. `get_hyponyms` on a broad word (e.g. `animal.n.01`)
can return a large result at higher depths.

### `get_meronyms` / `get_holonyms`

`synset_id` → list of synset entries tagged `relation`: `member`, `part`, or
`substance`.

- `get_meronyms` — parts/members/substances that make up this synset (car →
  engine, door — "has-a")
- `get_holonyms` — wholes this synset belongs to (wheel → car — "part-of")

### `get_antonyms`

| Parameter | Type | Description |
|---|---|---|
| `word` | `str` | e.g. `good`, `hot` |
| `pos` | `str \| None` | `n`/`v`/`a`/`r`/`s`, optional |

Antonymy is defined per word sense, not per synset as a whole — returns one
entry per sense of `word` that actually has an antonym recorded:
`{synset_id, definition, lemma, antonyms}`.

### `get_similarity`

| Parameter | Type | Description |
|---|---|---|
| `synset_id_1`, `synset_id_2` | `str` | e.g. `dog.n.01`, `cat.n.01` |
| `metric` | `str` | `path`, `wup` (default), or `lch` |

Semantic similarity between two already-disambiguated senses:
- `path` — `1 / (shortest hypernym path length + 1)`
- `wup` — Wu-Palmer, based on depth of the least common subsumer
- `lch` — Leacock-Chodorow; requires both synsets to share a part of speech,
  and is not bounded by 1 like the other two

Returns `{synset_id_1, synset_id_2, metric, score}`.

### `lemmatize`

| Parameter | Type | Description |
|---|---|---|
| `word` | `str` | Inflected word, e.g. `running`, `geese` |
| `pos` | `str \| None` | `n`/`v`/`a`/`r`. Omit to try all four |

Morphological base-form lookup (`nltk.morphy`) — not a synonym lookup.
`lemmatize("running", pos="v")` → `run`. With `pos` omitted, returns
`{word, lemmas_by_pos}` for every part of speech that matched.

---

## Typical session

```
lookup_synsets("bank", pos="n")
# -> bank.n.01 (financial institution), bank.n.09 (riverbank), ...

get_hypernyms("bank.n.01", depth=2)
# -> financial_institution.n.01 (distance 1), institution.n.01 (distance 2), ...

get_synonyms("bank.n.01")
# -> ["bank", "depository_financial_institution", "banking_concern", "banking_company"]

get_similarity("bank.n.01", "credit_union.n.01", metric="wup")
# -> high score: same branch of the hierarchy

get_meronyms("car.n.01")
# -> engine, door, window, ... each tagged relation="part"
```

---

## Running tests

```bash
cd platform/wordnet-mcp
python3 -m pytest tests/ -v
```

Tests run against the real WordNet corpus (downloaded on first run) — no
mocking, since WordNet is a fixed, offline dataset once downloaded.
