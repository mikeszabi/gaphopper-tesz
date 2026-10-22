import json
from pathlib import Path

import pytest

from gaphopper.llm import FakeLLM, LLMError, call_json
from gaphopper.schemas import P1Output


def fixture():
    return json.loads((Path(__file__).parent / "fixtures/p1_nurse.json").read_text())


def test_only_invalid_relevance_is_repaired():
    original = fixture()
    original["tesz_magok"][0]["relevance"] = "MEDIUM"
    original["tesz_magok"][2]["relevance"] = "HIGH"
    prompts, logs = [], []

    def answer(system, user):
        prompts.append(user)
        return json.dumps(original) if len(prompts) == 1 else json.dumps({"fixes": [
            {"index": 0, "relevance": "SUPPORTING"}, {"index": 2, "relevance": "CORE"}]})

    obj, data = call_json(FakeLLM(answer), "system", "job", P1Output,
                          log=lambda text, ok, usage: logs.append(ok))
    expected = fixture()
    expected["tesz_magok"][0]["relevance"] = "SUPPORTING"
    assert obj == P1Output.model_validate(expected)
    assert data == expected
    assert len(prompts) == 2 and "célzott mezőjavítás" in prompts[1]
    assert logs == [False, True]


def test_known_value_aliases_need_no_retry():
    """A vocab.yaml alias_ertek táblája: gyakori, egyértelmű eltérések újrahívás nélkül."""
    d = fixture()
    d["tesz_magok"][0]["relevance"] = "PERIPHERAL"
    d["tesz"][1]["frequency"] = "QUARTERLY"
    calls = []
    obj, _ = call_json(FakeLLM(lambda *a: calls.append(1) or json.dumps(d)), "system", "job", P1Output)
    assert len(calls) == 1
    assert obj.tesz_magok[0].relevance == "SUPPORTING" and obj.tesz[0].relevance == "SUPPORTING"
    assert obj.tesz[1].frequency == "MONTHLY"


@pytest.mark.parametrize("fixes", [
    [{"index": 0, "relevance": "MEDIUM"}],
    [{"index": 1, "relevance": "CORE"}],
    [{"index": 0, "relevance": "CORE"}, {"index": 0, "relevance": "CORE"}],
    [],
])
def test_bad_repairs_are_rejected(fixes):
    bad = fixture()
    bad["tesz_magok"][0]["relevance"] = "MEDIUM"
    answers = iter([json.dumps(bad), json.dumps({"fixes": fixes})])
    with pytest.raises(LLMError):
        call_json(FakeLLM(lambda *a: next(answers)), "system", "job", P1Output)


def test_other_errors_use_full_retry():
    bad = fixture()
    bad["tesz"][0]["expected_eqf"] = 99
    prompts = []
    def answer(system, user):
        prompts.append(user)
        return json.dumps(bad if len(prompts) == 1 else fixture())
    call_json(FakeLLM(answer), "system", "job", P1Output)
    assert "teljes JSON" in prompts[1]
