# Extraction benchmark results

Generated from `history.jsonl` — do not edit by hand.

Each full live run of the extraction benchmark (`python -m tests.benchmarks.extraction.run`, from
`lib/`) appends one summary line to `history.jsonl` and regenerates this page; commit both to
record the run. Partial runs (`--case`, `--mode`) are not recorded. The detailed per-stage report
of a run stays in `.reports/` (gitignored). To record a past run or re-render this page:
`python -m tests.benchmarks.extraction.history add <scores.json> [--note ...]` and
`python -m tests.benchmarks.extraction.history render`.

## Latest run

`20261105-161824` · commit `c36e6e3` · gpt-4o · 43 cases, 47 runs · judge gpt-4o-mini · *after the 1.0 release fixes (#173-#182), on blue-data 78e57b3*

| | entities F1 | facts F1 | props F1 | runs failing a check |
|---|---|---|---|---|
| **all** | 0.90 ▼0.02 `████▇██` | 0.83 ▼0.01 `▇▇▇▇▇▇▇` | 0.76 ▼0.08 `▆▇▇▇▆▇▇` | 3 |

Each cell: latest value, ▲/▼ change since the previous run, and the history as a sparkline (`▁` = 0, `█` = 1).

## Trend

```mermaid
%%{init: {"themeVariables": {"xyChart": {"plotColorPalette": "#2563eb, #16a34a, #dc2626"}}}}%%
xychart-beta
    title "Mean F1 per run"
    x-axis ["261010-1732", "261011-2154", "261011-2228", "261011-2253", "261012-0006", "261102-1836", "261105-1618"]
    y-axis "F1" 0 --> 1
    line [0.94, 0.92, 0.94, 0.94, 0.87, 0.92, 0.90]
    line [0.82, 0.78, 0.81, 0.80, 0.82, 0.85, 0.83]
    line [0.67, 0.78, 0.85, 0.78, 0.70, 0.84, 0.76]
```

*Lines: blue = entities, green = facts, red = properties.*

## By category

```mermaid
xychart-beta horizontal
    title "Latest run: mean F1 per category (weakest first)"
    x-axis ["long_text", "rules_policy", "subject_self", "lists_common_nouns", "schema_expansion_quality", "negation_hedging", "cross_document", "basic", "coreference", "typing_fit", "schema_reuse", "relation_extraction", "same_name", "properties_normalization"]
    y-axis "mean of entities, facts, properties F1" 0 --> 1
    bar [0.42, 0.69, 0.71, 0.73, 0.75, 0.80, 0.80, 0.82, 0.88, 0.90, 0.91, 0.91, 0.94, 0.95]
```

| category | runs | entities F1 | facts F1 | props F1 | failing a check |
|---|---|---|---|---|---|
| basic | 1 | 0.80 ▼0.20 `██████▇` | 0.67 ▼0.33 `██▆███▆` | 1.00 `███████` | 0 |
| coreference | 3 | 0.89 ▲0.10 `██▇▇▇▇█` | 0.75 ▲0.08 `▇▇▄▆▇▆▇` | 1.00 ▲0.33 `▆▆▆▆▆▆█` | 0 |
| cross_document | 3 | 0.84 ▼0.05 `█▆████▇` | 1.00 ▲0.07 `███████` | 0.56 ▼0.11 `▇████▆▅` | 0 |
| lists_common_nouns | 3 | 0.59 ▼0.33 `▇███▇█▅` | 0.62 ▼0.20 `▇█▆█▆▇▅` | 1.00 `▆██████` | 0 |
| long_text | 2 | 0.76 ▲0.07 `▇▇█▇▆▆▇` | 0.48 ▼0.10 `▄▆▇▅▅▅▄` | 0.00 ▼0.50 `▁▅▅▂▂▅▁` | 0 |
| negation_hedging | 3 | 0.83 ▲0.05 `██▇█▇▇▇` | 0.89 ▲0.33 `▅▅▅▆▇▅█` | 0.67 ▼0.33 `▃▃█▆▆█▆` | 0 |
| properties_normalization | 5 | 1.00 `█▇█████` | 0.93 ▼0.07 `▆▇█▇███` | 0.93 ▼0.07 `███▆▇██` | 1 |
| relation_extraction | 5 | 0.97 `████▇██` | 0.96 ▲0.03 `███▆▇██` | 0.80 ▼0.20 `▅▆▅▅▂█▇` | 0 |
| rules_policy | 2 | 0.83 ▲0.16 `▇███▆▆▇` | 0.75 ▼0.08 `█▅▆█▇▇▇` | 0.50 ▲0.50 `▁▃█▅▅▁▅` | 0 |
| same_name | 2 | 1.00 ▲0.07 `███████` | 0.83 ▲0.12 `▇▆▇▇▆▆▇` | 1.00 `████▅██` | 0 |
| schema_expansion_quality | 4 | 0.95 ▼0.05 `▇██████` | 0.77 ▼0.10 `▇▅▆▆▇▇▇` | 0.54 ▼0.32 `▅▅█▇▇▇▅` | 0 |
| schema_reuse | 7 | 1.00 ▲0.03 `███████` | 0.86 ▼0.14 `███▇▇█▇` | 0.86 ▼0.14 `████▇█▇` | 2 |
| subject_self | 3 | 0.86 ▼0.08 `██████▇` | 0.95 ▲0.28 `██▇▇█▆█` | 0.33 `▃▃▃▅▃▃▃` | 0 |
| typing_fit | 4 | 0.91 ▼0.09 `███████` | 0.78 ▼0.08 `▇▅▇▇▇▇▇` | 1.00 `███████` | 0 |

## By case (latest run)

<details><summary>Show all cases</summary>

| case · model · dedup | category | entities F1 | facts F1 | props F1 | failed checks |
|---|---|---|---|---|---|
| basic_extraction · gpt-4o · search | basic | 0.80 ▼0.20 `██████▇` | 0.67 ▼0.33 `██▆███▆` | 1.00 `███████` | — |
| coreference_epithets_company · gpt-4o · search | coreference | 0.86 ▲0.06 `▇█▅▅▆▇▇` | 0.86 ▲0.52 `▇▄▄▆▆▃▇` | 1.00 `███████` | — |
| coreference_pronoun_and_epithet · gpt-4o · search | coreference | 1.00 ▲0.20 `████▇▇█` | 1.00 `██▁████` | 1.00 ▲1.00 `▁▁▁▁█▁█` | — |
| coreference_two_people · gpt-4o · search | coreference | 0.80 ▲0.05 `███▇█▇▇` | 0.40 ▼0.27 `▆▇▇▄▆▆▄` | 1.00 `████▁██` | — |
| cross_document_alias · gpt-4o · search | cross_document | 0.67 `▆▅▆▆▆▆▆` | 1.00 `███████` | 1.00 `███████` | — |
| cross_document_entity_fusion · gpt-4o · search | cross_document | 1.00 `█▆█████` | 1.00 `█▆█████` | 0.67 ▼0.33 `▆██▆██▆` | — |
| cross_document_update · gpt-4o · search | cross_document | 0.86 ▼0.14 `██████▇` | 1.00 ▲0.20 `████▇▇█` | 0.00 `▆▆▆██▁▁` | — |
| common_noun_entity_classes · gpt-4o · search | lists_common_nouns | 0.96 ▲0.05 `███████` | 0.86 ▼0.09 `▇▇█▇██▇` | 1.00 `███████` | — |
| lists_recipe_ingredients · gpt-4o · search | lists_common_nouns | 0.00 ▼1.00 `▆██▆▃█▁` | 0.00 ▼1.00 `████▁█▁` | 1.00 `▁██████` | — |
| lists_tech_stack · gpt-4o · search | lists_common_nouns | 0.80 ▼0.03 `█████▇▇` | 1.00 ▲0.50 `▅█▁██▅█` | 1.00 `███████` | — |
| long_company_history · gpt-4o · search | long_text | 0.86 ▲0.30 `▇▇█▇▇▅▇` | 0.47 ▼0.03 `▇▆█▆▆▅▄` | 0.00 `▁▁▁▃▄▁▁` | — |
| long_meeting_notes · gpt-4o · search | long_text | 0.67 ▼0.17 `▆▇█▇▆▇▆` | 0.50 ▼0.17 `▁▅▇▅▅▆▅` | 0.00 ▼1.00 `▁██▁▁█▁` | — |
| negation_false_positive_trap · gpt-4o · search | negation_hedging | 0.50 `██▆█▅▅▅` | 1.00 ▲1.00 `▁▁▁▁█▁█` | 1.00 `▁▁█████` | — |
| negation_former_and_absent · gpt-4o · search | negation_hedging | 1.00 `███████` | 1.00 `███████` | 1.00 `███████` | — |
| negation_hypothetical_plans · gpt-4o · search | negation_hedging | 1.00 ▲0.14 `█████▇█` | 0.67 `▅▅▆█▅▆▆` | 0.00 ▼1.00 `▁▁▆▁▁█▁` | — |
| attribute_extraction_dense · gpt-4o · search | properties_normalization | 1.00 `█▅█▇███` | 1.00 `▆██▆███` | 1.00 `▆██▆███` | — |
| fit_value_to_property · gpt-4o · search | properties_normalization | 1.00 `██████` | 1.00 `██████` | 0.67 ▼0.33 `██▁██▆` | — |
| multi_valued_attributes · gpt-4o · search | properties_normalization | 1.00 `█▅█████` | 1.00 `███████` | 1.00 `████▆██` | — |
| values_dates_formats · gpt-4o · search | properties_normalization | 1.00 `███████` | 0.67 ▼0.33 `██████▆` | 1.00 `████▅██` | — |
| values_money_percent_units · gpt-4o · search | properties_normalization | 1.00 `███▇███` | 1.00 `▁▁█▆███` | 1.00 `███████` | norm |
| multi_fact_dense · gpt-4o · search | relation_extraction | 1.00 `████▁██` | 1.00 `▇███▁██` | 1.00 `▇███▁██` | — |
| relation_direction_acquisition · gpt-4o · search | relation_extraction | 1.00 `███████` | 1.00 `███▁███` | 0.00 ▼1.00 `██▁▁▁█▁` | — |
| relation_extraction_multi_type · gpt-4o · search | relation_extraction | 1.00 `███████` | 1.00 `███████` | 1.00 `███████` | — |
| relation_study_vs_work · gpt-4o · search | relation_extraction | 1.00 `███████` | 1.00 ▲0.14 `█▇▇▆█▇█` | 1.00 `▁▆█▁▁██` | — |
| relation_symmetric_and_implicit · gpt-4o · search | relation_extraction | 0.86 `▇▇▇█▇▇▇` | 0.80 `▇▇▇▇▇▇▇` | 1.00 `▁▁▁█▁██` | — |
| policy_leave_rules · gpt-4o · search | rules_policy | 1.00 ▲0.25 `████▇▇█` | 1.00 `█▆▇████` | 1.00 ▲1.00 `▁▆███▁█` | — |
| policy_requirement_not_instances · gpt-4o · search | rules_policy | 0.67 ▲0.07 `▆█▇█▄▅▆` | 0.50 ▼0.17 `▇▄▅▇▆▆▅` | 0.00 `▁▁█▁▁▁▁` | — |
| same_name_apple · gpt-4o · search | same_name | 1.00 ▲0.14 `█▇██▇▇█` | 1.00 ▲0.25 `▇▇▇▇▇▇█` | 1.00 `███████` | — |
| same_name_jordan · gpt-4o · search | same_name | 1.00 `███████` | 0.67 `▆▅▆▆▆▆▆` | 1.00 `████▁██` | — |
| expansion_covers_text · gpt-4o · search | schema_expansion_quality | 1.00 `▇██████` | 0.40 ▼0.27 `▁▁▁▄▆▆▄` | 1.00 `███████` | — |
| expansion_no_instance_classes · gpt-4o · search | schema_expansion_quality | 1.00 `███████` | 1.00 `█▆█▆███` | 0.00 ▼1.00 `▁▁████▁` | — |
| expansion_property_placement · gpt-4o · search | schema_expansion_quality | 1.00 `▇██▇███` | 1.00 ▲0.20 `██▇▇▇▇█` | 0.50 ▼0.30 `▅▇▅▅▇▇▅` | — |
| schema_expansion_new_property · gpt-4o · search | schema_expansion_quality | 0.80 ▼0.20 `▇▆█▇▇█▇` | 0.67 ▼0.33 `█▆█▆▆█▆` | 0.67 `▆▆█▆▆▆▆` | — |
| reuse_class_synonym · gpt-4o · llm | schema_reuse | 1.00 `███████` | 1.00 `███████` | 1.00 `███████` | — |
| reuse_class_synonym · gpt-4o · search | schema_reuse | 1.00 `███████` | 1.00 `█▆█████` | 1.00 `███████` | — |
| reuse_relation_synonym · gpt-4o · llm | schema_reuse | 1.00 ▲0.20 `▇███▇▇█` | 1.00 `▆▆█▁███` | 1.00 `███████` | — |
| reuse_relation_synonym · gpt-4o · search | schema_reuse | 1.00 `█▇▆█▇██` | 1.00 `██▆████` | 1.00 `███████` | schema |
| schema_reuse_synonym · gpt-4o · llm | schema_reuse | 1.00 `███████` | 0.00 ▼1.00 `██████▁` | 0.00 ▼1.00 `██████▁` | — |
| schema_reuse_synonym · gpt-4o · none | schema_reuse | 1.00 `███████` | 1.00 `████▁██` | 1.00 `████▁██` | schema |
| schema_reuse_synonym · gpt-4o · search | schema_reuse | 1.00 `███████` | 1.00 `███████` | 1.00 `███████` | — |
| subject_profile_third_person · gpt-4o · search | subject_self | 0.67 ▼0.33 `██████▆` | 1.00 ▲0.20 `█████▇█` | 1.00 `███████` | — |
| subject_resume_lines · gpt-4o · search | subject_self | 0.91 ▲0.08 `███▆█▇█` | 0.86 ▲0.46 `▇█▇▆▇▄▇` | 0.00 `▁▁▁▇▁▁▁` | — |
| subject_user_preamble · gpt-4o · search | subject_self | 1.00 `███████` | 1.00 ▲0.20 `██▇▇█▇█` | 0.00 `▁▁▁▁▁▁▁` | — |
| entity_typing_subclass · gpt-4o · search | typing_fit | 0.86 ▼0.14 `████▇█▇` | 1.00 `█▁█████` | 1.00 `███████` | — |
| fit_agency_not_company · gpt-4o · search | typing_fit | 0.80 ▼0.20 `▇▇▇█▇█▇` | 0.67 ▼0.33 `▆▆▆█▆█▆` | 1.00 `███████` | — |
| fit_titles_not_entities · gpt-4o · search | typing_fit | 1.00 `██▇████` | 0.80 `▇▇▇▇▇▇▇` | 1.00 `███████` | — |
| fit_university_not_company · gpt-4o · search | typing_fit | 1.00 `███████` | 0.67 `██▆▆▆▆▆` | 1.00 `███████` | — |

</details>

## All runs

| run | commit | models | cases | runs | entities | facts | props | failing a check | tokens | note |
|---|---|---|---|---|---|---|---|---|---|---|
| 20261105-161824 | `c36e6e3` | gpt-4o | 43 | 47 | 0.90 | 0.83 | 0.76 | 3 | 2.0M | after the 1.0 release fixes (#173-#182), on blue-data 78e57b3 |
| 20261102-183606 | `06b50f7` | gpt-4o | 43 | 47 | 0.92 | 0.85 | 0.84 | 3 | 2.0M | first run since 1.0 release work: LiteLLM client (5069c86), Python 3.12, enrich/expansion changes since 59fe703 |
| 20261012-000617 | `59fe703` | gpt-4o | 43 | 47 | 0.87 | 0.82 | 0.70 | 5 | 2.1M | stricter entity judge (class must fit; scores not comparable to earlier runs) + stage-aware id errors + stage-scope prompts |
| 20261011-225332 | `8014f43`* | gpt-4o | 43 | 47 | 0.94 | 0.80 | 0.78 | 4 | 2.7M | wrong-kind id errors, first version: hints pointed at other stages' tools; replaced by stage-aware hints, not kept |
| 20261011-222857 | `be9b335`* | gpt-4o | 43 | 47 | 0.94 | 0.81 | 0.85 | 6 | 2.2M | short handles (E1, R2, ...) instead of uuids in extraction |
| 20261011-215455 | `fb3438d` | gpt-4o | 43 | 47 | 0.92 | 0.78 | 0.78 | 5 | 2.6M | after P0/P1 fixes: #128 span required, #95 typed properties, #96 id typos, #139 |
| 20261010-173226 | `d4c49d1` | gpt-4o | 42 | 46 | 0.94 | 0.82 | 0.67 | 11 | 2.3M | first 42-case run; dom/rng failures overcounted (check was not subclass-aware yet) |

`*` = run made with uncommitted changes.
