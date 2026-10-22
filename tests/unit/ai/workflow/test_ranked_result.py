"""A result with several cars: a short summary in the chat, the ranked list on the cards."""

import copy
import json
from pathlib import Path

from app.ai.workflow import messages_he as he
from app.contracts.comparisons import ComparisonResult

FIXTURE = Path(__file__).parents[3] / "fakes" / "fixtures" / "success.json"


def _result(*deltas: tuple[str, int]) -> ComparisonResult:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))["compare_scenarios"]["data"]
    template = data["alternatives"][0]
    data["alternatives"] = []
    for key, delta in deltas:
        alternative = copy.deepcopy(template)
        alternative["optionKey"] = key
        alternative["delta"] = {"raw": f"{delta}.000000", "display": str(delta)}
        data["alternatives"].append(alternative)
    return ComparisonResult.model_validate(data)


def test_several_cars_get_a_short_summary_and_a_ranked_list() -> None:
    result = _result(("vehicle:CAR-10", 661), ("vehicle:CAR-06", 1013), ("vehicle:CAR-01", -286))
    labels = {"vehicle:CAR-06": "MG4", "vehicle:CAR-10": "ATTO 3", "vehicle:CAR-01": "PICANTO"}
    text = he.comparison_text(result, labels)
    assert "סידרתי לך את 3 הרכבים" in text and "MG4" in text and "1,013" in text
    assert "ATTO 3" not in text and "PICANTO" not in text  # the full list is on the cards
    assert "בכפתור למטה" in text
    assert he.ranking(result) == ["vehicle:CAR-06", "vehicle:CAR-10", "vehicle:CAR-01"]


def test_the_ranked_payload_can_be_staged() -> None:
    """The reply must pass the staging rule (only allowed keys, no amounts), or the turn fails
    with "we could not complete the reply" for every multi-car result."""
    from app.ai.infrastructure.persistence.store import _check_staged_payload

    result = _result(("vehicle:CAR-10", 661), ("vehicle:CAR-06", 1013))
    _check_staged_payload({"runId": "x", "optionKeys": he.ranking(result)})


def test_one_car_keeps_its_own_line_and_no_ranked_list() -> None:
    result = _result(("vehicle:CAR-10", 661))
    assert "ATTO 3" in he.comparison_text(result, {"vehicle:CAR-10": "ATTO 3"})
    assert he.ranking(result) == []
