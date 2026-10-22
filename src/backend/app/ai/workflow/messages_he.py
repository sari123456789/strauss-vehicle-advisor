"""Deterministic Hebrew texts: questions, confirmations, result summaries and error messages.

Amounts are formatted here from structured tool results only; model output never supplies a
number. Error texts are natural Hebrew; raw HTTP statuses, codes and trace IDs are never shown.
"""

from collections.abc import Iterable

from app.ai.workflow.extraction import Clarification, ClarificationCode
from app.contracts.catalogue import EligibilityStatus, OptionItem
from app.contracts.common import ErrorCode, FieldPath, IssueCode, Propulsion
from app.contracts.comparisons import ComparisonResult, MoneyAmount, ScenarioStatus
from app.contracts.representatives import AnswerView

FIELD_LABELS: dict[str, str] = {
    FieldPath.BASE_GROSS: "שכר הבסיס החודשי ברוטו",
    FieldPath.COMMISSIONS_GROSS: "העמלות החודשיות ברוטו",
    FieldPath.BONUS_GROSS: "הבונוס החודשי ברוטו",
    FieldPath.PENSION_EMPLOYEE_RATE: "שיעור הפרשת העובד לפנסיה",
    FieldPath.STUDY_FUND_EMPLOYEE_RATE: "שיעור הפרשת העובד לקרן השתלמות",
    FieldPath.CREDIT_POINTS: "מספר נקודות הזיכוי",
    FieldPath.OCCUPATION_ID: "העיסוק",
    FieldPath.ENTITLEMENT_GROUP: "קבוצת הזכאות לרכב",
    FieldPath.QUALIFYING_CHILDREN_COUNT: "מספר הילדים שטרם מלאו להם 24",
    FieldPath.CURRENT_KIND: "סוג ההסדר הנוכחי (רכב חברה, אחזקת רכב או ללא)",
    FieldPath.CURRENT_VEHICLE: "הרכב הנוכחי",
    FieldPath.CURRENT_PROPULSION: "סוג ההנעה של הרכב הנוכחי",
    FieldPath.CURRENT_PARTICIPATION: "ההשתתפות החודשית שלך ברכב הנוכחי",
    FieldPath.CURRENT_FAMILY_DEDUCTION: "ניכוי המסלול המשפחתי ברכב הנוכחי",
    FieldPath.CURRENT_FAMILY_DEDUCTION_INCLUDED: "האם ניכוי המסלול המשפחתי כלול בהשתתפות שדיווחת",
    FieldPath.CURRENT_ALLOWANCE: "סכום אחזקת הרכב החודשית",
}

PROPULSION_LABELS = {
    Propulsion.PETROL: "בנזין",
    Propulsion.HYBRID: "היברידי",
    Propulsion.PLUG_IN_HYBRID: "פלאג-אין",
    Propulsion.ELECTRIC: "חשמלי",
}

DISCLAIMER = (
    "חשוב לדעת: זו הערכה לפי הפרטים שלך והכללים שבמערכת (טבלאות שכר 2025 ושווי שימוש 2026), "
    "ולא תלוש שכר."
)
SHORT_DISCLAIMER = "(הערכה בלבד, לא תלוש שכר)"
"""After the first result in a conversation, only this short reminder is repeated."""


def field_label(path: str) -> str:
    return FIELD_LABELS.get(path, "פרט נוסף")


def shekels(amount: MoneyAmount, *, signed: bool = False) -> str:
    """Whole-shekel display string from the engine's `display` value."""
    value = int(amount.display)
    text = f"{abs(value):,} ₪"
    if value < 0:
        return f"-{text}"
    return f"+{text}" if signed and value > 0 else text


def ask_for_fields(fields: Iterable[str]) -> str:
    labels = [field_label(f) for f in fields]
    if len(labels) == 1:
        return f"כדי להמשיך חסר לי רק עוד פרט אחד: {labels[0]}. מה הוא?"
    return f"כדי להמשיך חסרים לי עוד שני פרטים: {labels[0]} ו{labels[1]}. אפשר לכתוב את שניהם יחד."


def unknown_field_guidance(fields: Iterable[str]) -> str:
    labels = ", ".join(field_label(f) for f in fields)
    return (
        f"אין בעיה. את {labels} אפשר בדרך כלל למצוא בתלוש השכר. "
        "בינתיים אפשר להמשיך במה שלא תלוי בזה, למשל לראות אילו רכבים מגיעים לך."
    )


_CLARIFICATIONS = {
    ClarificationCode.ACTUAL_OR_HYPOTHETICAL: (
        "רק לוודא לגבי {label}: זה הנתון האמיתי שלך היום (ואעדכן אותו), או בדיקה של מה יקרה אם?"
    ),
    ClarificationCode.NET_NOT_GROSS: (
        "הסכום שציינת הוא נטו, אבל החישוב שלי יוצא מ{label} (ברוטו). מה הסכום בברוטו?"
    ),
    ClarificationCode.SALARY_COMPOSITION: (
        "רק לוודא: הסכום הזה הוא שכר בסיס ברוטו, כל הברוטו (כולל עמלות ובונוסים), או נטו?"
    ),
    ClarificationCode.RATE_UNIT: "באיזה אחוז מדובר ב{label}? למשל 6%.",
    ClarificationCode.INVALID_VALUE: "לא הצלחתי לקלוט את הערך של {label}. אפשר לכתוב אותו שוב?",
    ClarificationCode.CONFLICTING_VALUES: "קיבלתי שני ערכים שונים ל{label}. איזה מהם נכון?",
    ClarificationCode.OCCUPATION_UNRESOLVED: (
        "לא מצאתי עיסוק רשמי שמתאים לתיאור. אפשר לכתוב את שם התפקיד כמו שהוא מופיע בחברה?"
    ),
    ClarificationCode.VEHICLE_UNRESOLVED: (
        "לא הצלחתי לזהות את הרכב בוודאות. אפשר לכתוב את הדגם ואת שנת הרישום?"
    ),
}


def clarification_text(items: list[Clarification]) -> str:
    lines = []
    for item in items[:2]:
        template = _CLARIFICATIONS[item.code]
        lines.append(template.format(label=field_label(item.field or "")))
    return "\n".join(lines)


def vehicle_candidates_text(names: list[str]) -> str:
    numbered = "\n".join(f"{i}. {name}" for i, name in enumerate(names, start=1))
    return f"יש כמה רכבים שמתאימים לתיאור. לאיזה מהם התכוונת?\n{numbered}"


def occupation_candidates_text(names: list[str]) -> str:
    numbered = "\n".join(f"{i}. {name}" for i, name in enumerate(names, start=1))
    return f"מצאתי כמה עיסוקים רשמיים שמתאימים לתיאור. איזה מהם הוא העיסוק שלך?\n{numbered}"


def labels_text(fields: Iterable[str]) -> str:
    labels = [field_label(f) for f in fields]
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " ו" + labels[-1]


def confirm_request_text(fields: list[str]) -> str:
    return (
        "לפני שאשווה למה שיש לך היום, רק לוודא שהפרטים האלה עדיין נכונים: "
        f"{labels_text(fields)}. (הם מופיעים בחלונית הצד.) "
        "אם משהו השתנה, פשוט לכתוב את הערך הנכון."
    )


def confirm_saved_text(fields: list[str], missing: list[str] | None = None) -> str:
    """The one confirmation per conversation before a calculation that rests on facts saved in
    an earlier conversation. Names the facts only; their values are in the side panel."""
    text = (
        "לפני שאחשב, רק לוודא שהפרטים ששמרנו משיחה קודמת עדיין נכונים: "
        f"{labels_text(fields)}. (הם מופיעים בחלונית הצד.) "
        'אם הכול נכון, מספיק לכתוב "כן"; אם משהו השתנה, פשוט לכתוב את הערך הנכון.'
    )
    if missing:
        text += " " + ask_for_fields(missing[:2])
    return text


def saved_facts_note(fields: list[str]) -> str:
    """One line under a result that used saved facts the employee did not restate here."""
    return (
        f"חישבתי לפי מה ששמרנו משיחה קודמת עבור {labels_text(fields)}. "
        "אם משהו מזה השתנה, אפשר לכתוב לי ואעדכן את החישוב."
    )


def confirmed_text(fields: list[str]) -> str:
    return f"מעולה, רשמתי ש{labels_text(fields)} עדיין נכונים."


def conflicts_text(fields: list[str]) -> str:
    if not fields:
        return "שמתי לב לסתירה בין הפרטים שמסרת לנתונים שבמערכת. כדאי לבדוק אותם לפני שנחשב."
    return f"שמתי לב לסתירה לגבי {labels_text(fields)}. כדאי לבדוק את הפרט הזה לפני שנחשב."


def still_missing_text(fields: list[str]) -> str:
    return f"שימו לב: כדי לחשב עד הסוף עדיין חסר {labels_text(fields)}."


PROPULSION_DIFFERS = (
    "סוג ההנעה שדיווחת לרכב הנוכחי שונה ממה שרשום בקטלוג, אז עוד לא חישבתי את המצב של היום. "
    "מה סוג ההנעה הנכון?"
)
OPTIONS_CHANGED = (
    "הפרטים השתנו מאז שהצגתי את רשימת הרכבים, אז ייתכן שהיא כבר לא מדויקת. "
    'אפשר לכתוב "אילו רכבים מתאימים לי?" ואציג רשימה מעודכנת.'
)


def eligibility_changed_text(labels: list[str]) -> str:
    return f"שימו לב: הזכאות ל{', '.join(labels)} השתנתה מאז שהצגתי את הרשימה."


def option_label(item: OptionItem) -> str:
    name = " ".join(p for p in (item.manufacturer_name, item.commercial_name) if p)
    if item.category.value == "waiver":
        return "ויתור על רכב חברה (אחזקת רכב)"
    return name or (item.catalogue_id or item.option_key)


def options_text(items: list[OptionItem], *, scope_complete: bool, unchecked: list[str]) -> str:
    eligible = [i for i in items if i.eligibility is EligibilityStatus.ELIGIBLE]
    if not eligible:
        return (
            "לפי הפרטים שלך לא מצאתי כרגע רכבים זמינים. "
            "אם משהו בפרופיל לא נכון, אפשר לתקן אותו כאן."
        )
    # The vehicles themselves are shown in the chat's side panel, not listed in the message.
    if len(eligible) == 1:
        lines = ["יש רכב אחד שמתאים לך 🚗 שמתי אותו בכפתור למטה, אפשר להציץ."]
    else:
        lines = [
            f"איזה כיף, יש לך {len(eligible)} רכבים לבחור מהם 🚗 "
            "שמתי את כולם בכפתור למטה, אפשר להציץ בנחת."
        ]
    if not scope_complete or unchecked:
        lines.append("יש עוד כמה מסלולים שלא בדקתי, כי חסר לי עליהם קצת מידע.")
    lines.append('רוצה שאבדוק כמה זה ישנה לך בנטו כל חודש לעומת היום? אפשר לבחור רכב אחד, כמה, או פשוט לכתוב "כולם" 😊')
    return "\n".join(lines)


_STATUS_TEXT = {
    ScenarioStatus.INELIGIBLE: "לא זמין לפי קבוצת הזכאות או הכללים",
    ScenarioStatus.NEEDS_INFORMATION: "חסר עוד מידע כדי לחשב",
    ScenarioStatus.UNSUPPORTED: "עוד לא נתמך בחישוב",
    ScenarioStatus.FAILED: "החישוב לא הושלם",
}


def _delta_phrase(delta: MoneyAmount) -> str:
    value = int(delta.display)
    if value > 0:
        return f"יישארו לך {shekels(delta)} יותר"
    if value < 0:
        return f"יישארו לך {shekels(delta)[1:]} פחות"
    return "אין שינוי"


def comparison_text(
    result: ComparisonResult, labels: dict[str, str], *, full_disclaimer: bool = True
) -> str:
    """Amounts from the Business result only. The full disclaimer is shown once per
    conversation; later results repeat a short reminder."""
    lines = []
    if result.current is not None and result.current.net is None:
        lines.append("את המצב של היום לא הצלחתי לחשב, אז אין כאן עדיין הפרש.")
    calculated = [
        s
        for s in result.alternatives
        if s.status is ScenarioStatus.CALCULATED and s.net is not None
    ]
    with_delta = [s for s in calculated if s.delta is not None]
    # The point of the advisor is the monthly difference from today, so it leads every line;
    # the net itself is only secondary context.
    if len(result.alternatives) == 1 and len(with_delta) == 1:
        s = with_delta[0]
        lines.append(
            f"עם {labels.get(s.option_key, s.option_key)}: {_delta_phrase(s.delta)} בחודש לעומת היום "
            f"(נטו משוער {shekels(s.net)})."
        )
    else:
        if with_delta:
            lines.append("ההפרש בנטו החודשי שלך לעומת היום:")
        ordered = sorted(
            result.alternatives,
            key=lambda s: -int(s.delta.display) if s.delta is not None else 10**9,
        )
        for scenario in ordered:
            label = labels.get(scenario.option_key, scenario.option_key)
            if scenario.status is ScenarioStatus.CALCULATED and scenario.delta is not None:
                lines.append(f"• {label}: {shekels(scenario.delta, signed=True)} בחודש")
            elif scenario.status is ScenarioStatus.CALCULATED and scenario.net is not None:
                lines.append(f"• {label}: נטו משוער {shekels(scenario.net)}")
            else:
                lines.append(f"• {label}: {_STATUS_TEXT.get(scenario.status, 'לא חושב')}")
        if len(with_delta) > 1:
            best = max(with_delta, key=lambda s: int(s.delta.display))
            lines.append(
                f"הכי משתלם לך: {labels.get(best.option_key, best.option_key)} — "
                f"{_delta_phrase(best.delta)} בחודש לעומת היום."
            )
    if result.kind.value == "hypothetical":
        lines.append('זו רק בדיקת "מה אם", הפרטים השמורים שלך לא השתנו.')
    lines.append(DISCLAIMER if full_disclaimer else SHORT_DISCLAIMER)
    return "\n".join(lines)


def pending_text(labels: list[str]) -> str:
    joined = ", ".join(labels)
    return (
        f"בשביל {joined} חסר פרט שרק נציג התחבורה יכול לתת, אז פניתי אליו. "
        "בינתיים אפשר להמשיך בכל דבר אחר, ואעדכן כאן כשתגיע תשובה."
    )


def representative_created_text(label: str) -> str:
    return f"שלחתי לנציג התחבורה שאלה לגבי {label}. אפשר להמשיך כרגיל, ואעדכן כאן כשתגיע תשובה."


def representative_answer_text(
    label: str,
    field_code: str | None = None,
    answer: AnswerView | None = None,
    *,
    recalculated: bool = False,
) -> str:
    """The representative's answer in plain words: what was answered, from when, and whether
    it counts for this calculation month. The value is the representative's, never computed."""
    lines = [f"הגיעה תשובה מנציג התחבורה לגבי {label} 🎉"]
    value = answer.value if answer is not None else None
    if value:
        if field_code == "alternative.participation":
            amount = f"{int(float(value)):,} ₪" if value.replace(".", "", 1).isdigit() else value
            lines.append(f"דמי ההשתתפות החודשיים הם {amount}.")
        elif field_code == "employee.entitlementGroup":
            lines.append(f"קבוצת הזכאות שלך היא {value}.")
        elif field_code == "currentArrangement.propulsion":
            try:
                lines.append(f"סוג ההנעה של הרכב שלך: {PROPULSION_LABELS[Propulsion(value)]}.")
            except ValueError:
                lines.append(f"סוג ההנעה של הרכב שלך: {value}.")
        elif field_code == "currentArrangement.vehicle":
            lines.append("הנציג השלים את פרטי הרכב הנוכחי שלך.")
        else:
            lines.append(f"התשובה: {value}.")
    if answer is not None and (answer.effective_from or answer.effective_to):
        start = answer.effective_from.strftime("%d.%m.%Y") if answer.effective_from else None
        end = answer.effective_to.strftime("%d.%m.%Y") if answer.effective_to else None
        if start and end:
            lines.append(f"זה בתוקף מ-{start} עד {end}.")
        elif start:
            lines.append(f"זה בתוקף החל מ-{start}.")
        else:
            lines.append(f"זה בתוקף עד {end}.")
    if answer is not None and answer.application is not None and answer.application.value == "not_applicable":
        lines.append("שימו לב: התשובה לא חלה על חודש החישוב של השיחה הזו, אז החישוב לא משתנה.")
    elif recalculated:
        lines.append("עדכנתי את החישוב לפי התשובה 👇")
    else:
        lines.append("רוצה שנבדוק עכשיו כמה זה ישנה לך בנטו כל חודש? 😊")
    return "\n".join(lines)


def representative_undetermined_text(label: str) -> str:
    return (
        f"נציג התחבורה לא יכול היה לקבוע את המידע לגבי {label}, אז את החלופה הזו לא אוכל לחשב. "
        "כל השאר ממשיך כרגיל."
    )


def not_saved_text(fields: list[str]) -> str:
    """Safety net: a value given inside a conditional sentence was calculated, not saved."""
    return (
        f"לא עדכנתי את {labels_text(fields)} בפרטים השמורים שלך. "
        "אם צריך לעדכן, אפשר פשוט לכתוב לי."
    )


def updated_text(fields: list[str], *, hypothetical: bool, stale_runs: bool) -> str:
    labels = ", ".join(field_label(f) for f in fields)
    if hypothetical:
        text = f'רשמתי לבדיקת "מה אם": {labels}. הפרטים השמורים שלך לא השתנו.'
    else:
        text = f"עדכנתי את {labels} 👍"
    if stale_runs:
        text += " חישובים קודמים מבוססים על הנתונים הקודמים, אז הם כבר לא מעודכנים."
    return text


def history_text(
    result: ComparisonResult, labels: dict[str, str], *, stale: bool, full_disclaimer: bool = True
) -> str:
    header = "זה החישוב הקודם, כמו שנשמר"
    if stale:
        header += " (הנתונים השתנו מאז, אז הוא כבר אינו עדכני)"
    body = comparison_text(result, labels, full_disclaimer=full_disclaimer)
    return f"{header}:\n{body}"


NO_RESULTS_YET = "עדיין לא ביצענו חישוב בשיחה הזו. רוצה שנבדוק כמה רכב מסוים ישנה לך בחודש?"
NOTHING_TO_CONFIRM = "אין כרגע פרטים שמחכים לאישור. במה אפשר לעזור?"
CANCELLED = "בסדר גמור, עצרתי. אפשר להתחיל משהו חדש מתי שרוצים."
UNCLEAR = 'ייתכן שלא הבנתי נכון. אפשר לנסח אחרת? למשל: "אילו רכבים מתאימים לי?"'
HELP = (
    "היי! 👋 אני כאן כדי לעזור לבחור רכב חברה ולראות כמה זה ישנה לך בנטו כל חודש לעומת היום. "
    "רוצה שנתחיל מהרכבים שמגיעים לך?"
)
GENERAL_FALLBACK = (
    "על זה קשה לי לענות כרגע. אבל אפשר להמשיך לבדוק רכבים, "
    'למשל: "אילו רכבים מתאימים לי?" או "כמה נטו עם ATTO 3?"'
)
OUT_OF_SCOPE = (
    "אני כאן כדי לעזור בנושאי רכב החברה וההשפעה שלהם על השכר 🙂 "
    "על זה לא אוכל לעזור, אבל אשמח לבדוק איתך רכבים או לחשב כמה רכב מסוים ישנה לך בחודש."
)
GENERAL_ALREADY_SENT = "השאלה כבר אצל נציג התחבורה ✓ אכתוב לך כאן ברגע שתגיע תשובה."
OFFER_DECLINED = "בסדר גמור, לא שלחתי. במה עוד אפשר לעזור?"


def offer_question_text(question: str) -> str:
    return (
        "על זה הכי טוב שיענה נציג התחבורה, כי זה תלוי בנהלים של החברה.\n"
        f"לשלוח לו את השאלה: \"{question}\"?"
    )


def general_question_sent_text() -> str:
    return "שלחתי את השאלה לנציג התחבורה 📨 אפשר להמשיך כרגיל, ואכתוב לך כאן ברגע שתגיע תשובה."


def general_answer_text(text: str | None) -> str:
    if not text:
        return "הגיעה תשובה מנציג התחבורה לשאלה שלך 🎉"
    return f"הגיעה תשובה מנציג התחבורה לשאלה שלך 🎉\n{text}"


ASK_WHICH_VEHICLE = "לאיזה רכב התכוונת? אפשר לכתוב את השם או את המספר שלו ברשימה."
BUDGET_EXCEEDED = "זה קצת הרבה בבת אחת 🙂 אפשר לפצל לשאלות קטנות יותר?"
LLM_RATE_LIMITED = "יש עכשיו עומס קטן. אפשר לנסות שוב בעוד רגע?"
LLM_QUOTA_EXHAUSTED = (
    "אני לוקח הפסקה קטנה 🙂 בינתיים אפשר להציץ בקטלוג ובהשוואות, ונחזור לדבר עוד מעט."
)
LLM_UNAVAILABLE = "משהו השתבש אצלי בהבנת ההודעה. אפשר לנסות שוב?"
SAME_AS_BEFORE_IGNORED = "התשובה הזו כבר טופלה."

_ERRORS = {
    ErrorCode.NOT_FOUND: "לא מצאתי את המידע הזה.",
    ErrorCode.VERSION_CONFLICT: (
        "הנתונים עודכנו בינתיים, אז טענתי אותם מחדש. אפשר לאשר שוב את השינוי?"
    ),
    ErrorCode.OPERATION_PAYLOAD_MISMATCH: "הבקשה התנגשה בבקשה קודמת. אפשר לנסות שוב?",
    ErrorCode.INVALID_STATE_TRANSITION: "את זה אי אפשר לעשות בשלב הזה של השיחה.",
    ErrorCode.INVALID_BUSINESS_INPUT: "אחד הערכים לא נראה תקין. אפשר לבדוק ולכתוב אותו שוב?",
    ErrorCode.INPUT_OUT_OF_SUPPORTED_RANGE: "אחד הערכים מחוץ לטווח שהמערכת יודעת לחשב.",
    ErrorCode.GROSS_UP_NOT_CONVERGED: "לא הצלחתי להשלים את חישוב החזר החשמל לרכב הזה.",
    ErrorCode.RATE_LIMITED: LLM_RATE_LIMITED,
    ErrorCode.UNAUTHENTICATED: "צריך להתחבר מחדש כדי להמשיך.",
    ErrorCode.FORBIDDEN: "אין הרשאה לפעולה הזו.",
}
GENERIC_ERROR = "משהו השתבש לרגע. אפשר לנסות שוב?"


def error_text(code: ErrorCode | None) -> str:
    return _ERRORS.get(code, GENERIC_ERROR) if code is not None else GENERIC_ERROR


def issue_blocks_text(code: IssueCode) -> str:
    return {
        IssueCode.PENDING_REPRESENTATIVE: "ממתין לתשובת נציג",
        IssueCode.INELIGIBLE: "לא זמין לפי הכללים",
        IssueCode.MISSING_INPUT: "חסר מידע",
    }.get(code, "לא חושב")


# --- Vehicles named by the employee (resolved against the catalogue) ---------------------------


def vehicle_choices_text(name: str, labels: list[str]) -> str:
    lines = "\n".join(f"{index}. {label}" for index, label in enumerate(labels, start=1))
    return f'יש כמה רכבים שמתאימים ל"{name}":\n{lines}\nלאיזה מהם התכוונת?'


def vehicle_not_in_catalogue_text(name: str) -> str:
    return (
        f'לא מצאתי רכב בשם "{name}" בקטלוג של החברה, סליחה. '
        'אולי בכתיב אחר? אפשר גם לכתוב "אילו רכבים מתאימים לי?" ואציג את כל הרשימה.'
    )


def vehicle_not_eligible_text(labels: list[str]) -> str:
    names = ", ".join(labels)
    return (
        f"{names} נמצא בקטלוג, אבל לפי הפרטים בפרופיל שלך הוא אינו בין החלופות שמגיעות לך. "
        'רוצה לראות מה כן מגיע לך? אפשר לכתוב "אילו רכבים מתאימים לי?". '
        "ואם משהו בפרופיל לא נכון, אפשר לתקן אותו כאן."
    )


def vehicle_eligibility_unknown_text(labels: list[str]) -> str:
    names = ", ".join(labels)
    return f"{names} נמצא בקטלוג, אבל כדי לדעת אם הוא מגיע לך חסר לי עוד מידע."


# --- Where the information comes from ----------------------------------------------------------

SOURCE_LABELS = {
    "employee": "דיווח העובד",
    "representative": "נציג התחבורה",
    "catalogue": "קטלוג הרכבים של החברה",
    "rules": "כללי החברה",
    "tax_dataset": "נתוני רשות המסים",
}
"""Labels for `SourceType` values."""


def data_sources_text(fields: list[tuple[str, str, str | None]]) -> str:
    """`fields`: (field path, status, source) of the saved profile; never values."""
    by_source: dict[str, list[str]] = {}
    missing: list[str] = []
    for path, status, source in fields:
        if status == "missing":
            missing.append(field_label(path))
            continue
        label = SOURCE_LABELS.get(source or "", "נשמר בפרופיל מראש")
        by_source.setdefault(label, []).append(field_label(path))
    lines = ["שאלה טובה! ככה נאסף המידע שעליו מבוססות התשובות שלי:"]
    for label, names in by_source.items():
        lines.append(f"• {', '.join(names)}: מהפרופיל השמור שלך. מקור: {label}.")
    if missing:
        lines.append(f"• עדיין חסרים בפרופיל: {', '.join(missing)}.")
    lines.append(
        "• רשימת הרכבים והזכאות: קטלוג הרכבים וכללי הזכאות של החברה במערכת. "
        "שווי השימוש: נתוני רשות המסים."
    )
    lines.append(
        "הפרטים האישיים הם כפי שדווחו, וייתכן שחלקם כבר לא מעודכנים. אפשר לראות אותם בכרטיס "
        '"הנתונים שלי" שליד השיחה, ולתקן כל פרט כאן בשיחה.'
    )
    return "\n".join(lines)


# --- The transportation representative ---------------------------------------------------------

REPRESENTATIVE_HELP = (
    "בשמחה. נציג התחבורה עוזר כשחסר פרט שאי אפשר לדעת מהפרופיל: קבוצת הזכאות שלך, הרכב שיש "
    "לך היום או סוג ההנעה שלו. איזה פרט לברר מול הנציג?"
)
REPRESENTATIVE_HELP_AGAIN = (
    "כדי לשלוח שאלה לנציג צריך לדעת איזה פרט חסר, למשל: "
    '"לא ידועה לי קבוצת הזכאות שלי". אפשר גם להמשיך בינתיים, למשל: "אילו רכבים מתאימים לי?"'
)
