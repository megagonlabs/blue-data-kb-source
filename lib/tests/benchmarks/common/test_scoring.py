"""Offline tests for the benchmark's entity scoring: a match needs the right class,
not only the right name (#134)."""
import json
from types import SimpleNamespace

import pytest

from blue_data_kb_source import MemoryKBSource

from . import scoring


@pytest.mark.parametrize("view, named_after_itself", [
    ({"name": "Go", "classes": ["GO"]}, True),
    ({"name": "PostgreSQL", "classes": ["POSTGRESQL"]}, True),
    ({"name": "hypertension", "classes": ["HYPERTENSION"]}, True),
    ({"name": "Rust", "classes": ["RUST (a kind of PROGRAMMING_LANGUAGE)"]}, False),
    ({"name": "Go", "classes": ["GO", "PROGRAMMING_LANGUAGE"]}, False),
    ({"name": "Go", "classes": ["PROGRAMMING_LANGUAGE"]}, False),
    ({"name": "Bob", "classes": []}, False),
])
def test_a_class_named_after_the_entity_itself(view, named_after_itself):
    assert scoring._named_after_itself(view) is named_after_itself


async def test_the_judge_sees_every_class_with_its_superclasses():
    kb = MemoryKBSource(name="kb")
    kb.create_database("db")
    lang = await kb.declare_class("db", "PROGRAMMING_LANGUAGE", "A language")
    rust = await kb.declare_class("db", "RUST", "Rust")
    await kb.add_is_subclass_of("db", rust, lang)
    tool = await kb.declare_class("db", "TOOL", "A tool")
    rid = await kb.add_entity("db", "Rust", class_id=rust)
    await kb.attach_entity_class("db", rid, tool)
    view = scoring._entity_view(kb, "db", {"id": rid, "name": "Rust", "class_name": "RUST"})
    assert view == {"name": "Rust", "classes": ["RUST (a kind of PROGRAMMING_LANGUAGE)", "TOOL"]}


class _MatchEverything:
    """A judge that pairs expected[i] with actual[i], recording what it was sent."""

    def __init__(self):
        self.sent = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        self.sent.append(kwargs["messages"])
        n = kwargs["messages"][1]["content"].split("Actual:\n")[1].count("\n") + 1
        args = json.dumps({"matches": [{"expected_index": i, "actual_index": i} for i in range(n)]})
        tc = SimpleNamespace(function=SimpleNamespace(arguments=args))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[tc]))])


async def test_an_entity_typed_by_its_own_name_is_not_a_match_whatever_the_judge_says():
    kb = MemoryKBSource(name="kb")
    kb.create_database("db")
    go_cls = await kb.declare_class("db", "GO", "Go")
    go = await kb.add_entity("db", "Go", class_id=go_cls)
    judge = _MatchEverything()
    views = [scoring._entity_view(kb, "db", {"id": go, "name": "Go"})]
    matches = await scoring.judge_alignment([{"name": "Go", "class": "PROGRAMMING_LANGUAGE"}], views,
                                            "entity", judge, "gpt-4o-mini")
    assert len(matches) == 1   # the judge (here) matches by name...
    assert scoring._named_after_itself(views[0])   # ...which score_case then drops
    assert "A class named after the entity itself" in judge.sent[0][0]["content"]
