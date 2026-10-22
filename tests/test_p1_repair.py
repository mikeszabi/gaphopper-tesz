import json
from pathlib import Path

import pytest

from gaphopper.llm import FakeLLM, LLMError, call_json
from gaphopper.schemas import P1Output


def fixture():
    return json.loads((Path(__file__).parent / "fixtures/p1_nurse.json").read_text())


def test_only_invalid_relevance_is_repaired():
    original = fixture()
    original["tesz"][0]["relevance"] = "PERIPHERAL"
    original["tesz"][2]["relevance"] = "SECONDARY"
    prompts, logs = [], []

    def answer(system, user):
        prompts.append(user)
        return json.dumps(original) if len(prompts) == 1 else json.dumps({"fixes": [
            {"index": 0, "relevance": "SUPPORTING"}, {"index": 2, "relevance": "CORE"}]})

    obj, data = call_json(FakeLLM(answer), "system", "job", P1Output,
                          log=lambda text, ok, usage: logs.append(ok))
    expected = fixture()
    expected["tesz"][0]["relevance"] = "SUPPORTING"
    assert obj == P1Output.model_validate(expected)
    assert data == expected
    assert len(prompts) == 2 and "célzott mezőjavítás" in prompts[1]
    assert logs == [False, True]


@pytest.mark.parametrize("fixes", [
    [{"index": 0, "relevance": "PERIPHERAL"}],
    [{"index": 1, "relevance": "CORE"}],
    [{"index": 0, "relevance": "CORE"}, {"index": 0, "relevance": "CORE"}],
    [],
])
def test_bad_repairs_are_rejected(fixes):
    bad = fixture()
    bad["tesz"][0]["relevance"] = "PERIPHERAL"
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
