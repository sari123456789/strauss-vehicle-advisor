"""Grounding for model-written prose.

The model never sees or writes amounts. It may only reference named placeholders such as
`{{net:vehicle:CAR-10}}`; code substitutes the engine's display values. A text is rejected (and
the deterministic template used instead) when it contains any digit outside a placeholder or an
unknown placeholder, so no invented number can reach the employee.
"""

import re

from app.ai.workflow.messages_he import shekels
from app.contracts.comparisons import ComparisonResult, ScenarioStatus

_PLACEHOLDER = re.compile(r"\{\{([a-z]+:[A-Za-z0-9:_-]+)\}\}")
_DIGIT = re.compile(r"[0-9٠-٩۰-۹]")


def comparison_placeholders(
    result: ComparisonResult, labels: dict[str, str]
) -> tuple[dict[str, str], dict[str, str]]:
    """(values for code, descriptions for the model) keyed by placeholder name."""
    values: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    if result.current is not None and result.current.net is not None:
        values["net:current"] = shekels(result.current.net)
        descriptions["net:current"] = "הנטו המשוער בהסדר הנוכחי"
    for scenario in result.alternatives:
        if scenario.status is not ScenarioStatus.CALCULATED or scenario.net is None:
            continue
        label = labels.get(scenario.option_key, scenario.option_key)
        values[f"net:{scenario.option_key}"] = shekels(scenario.net)
        descriptions[f"net:{scenario.option_key}"] = f"הנטו המשוער עבור {label}"
        if scenario.delta is not None:
            values[f"delta:{scenario.option_key}"] = shekels(scenario.delta, signed=True)
            descriptions[f"delta:{scenario.option_key}"] = f"ההפרש בנטו עבור {label} לעומת היום"
    with_delta = [
        s
        for s in result.alternatives
        if s.status is ScenarioStatus.CALCULATED and s.delta is not None
    ]
    if len(with_delta) > 1:
        # Point the explanation at the most worthwhile car, not an arbitrary one.
        best = max(with_delta, key=lambda s: int(s.delta.display))
        keep = {f"delta:{best.option_key}", "net:current"}
        # The model sees only the best car's difference, so it cannot single out another car.
        descriptions = {k: v for k, v in descriptions.items() if k in keep}
        descriptions[f"delta:{best.option_key}"] += " (הרכב המשתלם ביותר)"
    return values, descriptions


def render_grounded(text: str, values: dict[str, str]) -> str | None:
    """Substituted text, or None when the text is not safely grounded."""
    names = _PLACEHOLDER.findall(text)
    if any(name not in values for name in names):
        return None
    without = _PLACEHOLDER.sub("", text)
    if _DIGIT.search(without) or "{{" in without or "}}" in without:
        return None
    rendered = _PLACEHOLDER.sub(lambda match: values[match.group(1)], text).strip()
    return rendered or None
