"""Deterministic Hebrew texts: questions, confirmations, result summaries and error messages.

Amounts are formatted here from structured tool results only; model output never supplies a
number. Error texts are natural Hebrew; raw HTTP statuses, codes and trace IDs are never shown.
"""

from collections.abc import Iterable

from app.ai.workflow.extraction import Clarification, ClarificationCode
from app.contracts.catalogue import EligibilityStatus, OptionItem
from app.contracts.common import ErrorCode, FieldPath, IssueCode, Propulsion
from app.contracts.comparisons import ComparisonResult, MoneyAmount, ScenarioStatus

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
    "החישוב הוא הערכה לפי המידע שמסרת והכללים שבמערכת (טבלאות שכר 2025 ושווי שימוש 2026), "
    "ואינו תלוש שכר."
)


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
        return f"כדי להמשיך, חסר לי {labels[0]}. מה הערך?"
    return f"כדי להמשיך, חסרים לי {labels[0]} ו{labels[1]}. אפשר לכתוב את שניהם יחד."


def unknown_field_guidance(fields: Iterable[str]) -> str:
    labels = ", ".join(field_label(f) for f in fields)
    return (
        f"אין בעיה. את {labels} אפשר בדרך כלל למצוא בתלוש השכר או לברר מול מחלקת השכר. "
        "בינתיים אוכל לעזור בדברים שלא תלויים בזה, למשל להציג את הרכבים שמתאימים לך."
    )


_CLARIFICATIONS = {
    ClarificationCode.ACTUAL_OR_HYPOTHETICAL: (
        "לגבי {label}: האם זה הנתון האמיתי שלך היום (ואעדכן אותו), או רק בדיקה של מה יקרה אם?"
    ),
    ClarificationCode.NET_NOT_GROSS: (
        "ציינת סכום נטו, אבל החישוב מבוסס על {label} (ברוטו). מה הסכום הזה?"
    ),
    ClarificationCode.SALARY_COMPOSITION: (
        "רק כדי לוודא: הסכום שציינת הוא שכר בסיס ברוטו, סך כל הברוטו (כולל עמלות ובונוסים), או נטו?"
    ),
    ClarificationCode.RATE_UNIT: "באיזה אחוז מדובר ב{label}? למשל 6%.",
    ClarificationCode.INVALID_VALUE: "לא הצלחתי להבין את הערך של {label}. אפשר לכתוב אותו שוב?",
    ClarificationCode.CONFLICTING_VALUES: "ציינת שני ערכים שונים ל{label}. מה הערך הנכון?",
    ClarificationCode.OCCUPATION_UNRESOLVED: (
        "לא מצאתי עיסוק רשמי שמתאים לתיאור. אפשר לכתוב את שם התפקיד כפי שהוא מופיע בחברה?"
    ),
    ClarificationCode.VEHICLE_UNRESOLVED: (
        "לא הצלחתי לזהות את הרכב בוודאות. אפשר לציין דגם ושנת רישום מדויקים?"
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
    return f"מצאתי כמה רכבים מתאימים לתיאור. לאיזה מהם התכוונת?\n{numbered}"


def occupation_candidates_text(names: list[str]) -> str:
    numbered = "\n".join(f"{i}. {name}" for i, name in enumerate(names, start=1))
    return f"מצאתי כמה עיסוקים רשמיים שמתאימים לתיאור. איזה מהם הוא העיסוק שלך?\n{numbered}"


def labels_text(fields: Iterable[str]) -> str:
    labels = [field_label(f) for f in fields]
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " ו" + labels[-1]


def confirm_request_text(fields: list[str]) -> str:
    return (
        "לפני שאציג הפרש לעומת היום, אפשר לאשר שהפרטים השמורים עדיין נכונים: "
        f"{labels_text(fields)}? הם מופיעים בחלונית הצד. "
        "אם משהו השתנה, אפשר פשוט לכתוב את הערך הנכון."
    )


def confirmed_text(fields: list[str]) -> str:
    return f"תודה, אישרתי ש{labels_text(fields)} עדיין נכונים."


def conflicts_text(fields: list[str]) -> str:
    if not fields:
        return "שמתי לב לסתירה בין הפרטים שמסרת לנתונים במערכת. כדאי לבדוק אותם לפני חישוב."
    return f"שמתי לב לסתירה לגבי {labels_text(fields)}. כדאי לבדוק את הפרט הזה לפני חישוב."


def still_missing_text(fields: list[str]) -> str:
    return f"לתשומת לבך: לחישוב מלא עדיין חסר {labels_text(fields)}."


PROPULSION_DIFFERS = (
    "סוג ההנעה שדיווחת לרכב הנוכחי שונה מהרשום בקטלוג, ולכן לא חישבתי את ההסדר הנוכחי. "
    "מה סוג ההנעה הנכון?"
)
OPTIONS_CHANGED = (
    "הפרטים השתנו מאז שהצגתי את רשימת החלופות, ולכן ייתכן שהיא כבר לא מדויקת. "
    'אפשר לבקש "אילו רכבים מתאימים לי?" כדי לראות רשימה מעודכנת.'
)


def eligibility_changed_text(labels: list[str]) -> str:
    return f"הזכאות ל{', '.join(labels)} השתנתה מאז שהוצגה הרשימה."


def option_label(item: OptionItem) -> str:
    name = " ".join(p for p in (item.manufacturer_name, item.commercial_name) if p)
    if item.category.value == "waiver":
        return "ויתור על רכב חברה (אחזקת רכב)"
    return name or (item.catalogue_id or item.option_key)


def options_text(items: list[OptionItem], *, scope_complete: bool, unchecked: list[str]) -> str:
    eligible = [i for i in items if i.eligibility is EligibilityStatus.ELIGIBLE]
    if not eligible:
        return "לפי המידע שמסרת לא מצאתי חלופות זמינות כרגע."
    lines = ["אלה החלופות שמתאימות לך לפי המידע שמסרת והכללים במערכת:"]
    for index, item in enumerate(eligible, start=1):
        details = []
        if item.propulsion:
            details.append(PROPULSION_LABELS[item.propulsion])
        if item.group:
            details.append(f"קבוצה {item.group.value}")
        if item.track and item.track.value == "family":
            details.append("מסלול משפחתי")
        suffix = f" ({', '.join(details)})" if details else ""
        lines.append(f"{index}. {option_label(item)}{suffix}")
    if not scope_complete or unchecked:
        lines.append("לתשומת לבך: לא כל המסלולים נבדקו, כי חסר מידע לבדיקת חלק מהם.")
    lines.append("אפשר לבקש לחשב את הנטו לחלופה אחת, לכמה או לכולן.")
    return "\n".join(lines)


_STATUS_TEXT = {
    ScenarioStatus.INELIGIBLE: "לא זמין לפי קבוצת הזכאות או הכללים",
    ScenarioStatus.NEEDS_INFORMATION: "חסר מידע לחישוב",
    ScenarioStatus.UNSUPPORTED: "לא נתמך בחישוב ההדגמה",
    ScenarioStatus.FAILED: "החישוב לא הושלם",
}


def comparison_text(result: ComparisonResult, labels: dict[str, str]) -> str:
    lines = []
    if result.current is not None and result.current.net is not None:
        lines.append(f"הנטו המשוער היום: {shekels(result.current.net)}")
    elif result.current is not None:
        lines.append("את הנטו הנוכחי לא ניתן היה לחשב, ולכן לא מוצג הפרש.")
    for scenario in result.alternatives:
        label = labels.get(scenario.option_key, scenario.option_key)
        if scenario.status is ScenarioStatus.CALCULATED and scenario.net is not None:
            line = f"• {label}: נטו משוער {shekels(scenario.net)}"
            if scenario.delta is not None:
                line += f" (הפרש {shekels(scenario.delta, signed=True)} לעומת היום)"
            lines.append(line)
        else:
            lines.append(f"• {label}: {_STATUS_TEXT.get(scenario.status, 'לא חושב')}")
    if result.kind.value == "hypothetical":
        lines.append('זו בדיקת "מה אם" בלבד; הפרטים השמורים שלך לא השתנו.')
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def pending_text(labels: list[str]) -> str:
    joined = ", ".join(labels)
    return (
        f"עבור {joined} חסר פרט שאפשר לקבל רק מנציג התחבורה. פנינו לנציג; "
        "בינתיים אפשר להמשיך לשאול על כל דבר אחר, ואעדכן כשתגיע תשובה."
    )


def representative_created_text(label: str) -> str:
    return f"שלחתי שאלה לנציג התחבורה לגבי {label}. השיחה ממשיכה כרגיל, ואעדכן כאן כשתתקבל תשובה."


def representative_answer_text(label: str) -> str:
    return f"התקבלה תשובה מנציג התחבורה לגבי {label}."


def representative_undetermined_text(label: str) -> str:
    return (
        f"נציג התחבורה לא יכול היה לקבוע את המידע לגבי {label}, ולכן לא ניתן לחשב את החלופה הזו. "
        "שאר הבדיקות ממשיכות כרגיל."
    )


def updated_text(fields: list[str], *, hypothetical: bool, stale_runs: bool) -> str:
    labels = ", ".join(field_label(f) for f in fields)
    if hypothetical:
        text = f'רשמתי לבדיקת "מה אם": {labels}. הפרטים השמורים שלך לא השתנו.'
    else:
        text = f"עדכנתי את {labels}."
    if stale_runs:
        text += " חישובים קודמים מבוססים על הנתונים הקודמים ואינם מעודכנים עוד."
    return text


def history_text(result: ComparisonResult, labels: dict[str, str], *, stale: bool) -> str:
    header = "זו ההשוואה הקודמת כפי שנשמרה"
    if stale:
        header += " (היא מבוססת על נתונים שהשתנו מאז, ולכן אינה עדכנית)"
    return f"{header}:\n{comparison_text(result, labels)}"


NO_RESULTS_YET = "עדיין לא ביצענו חישוב בשיחה הזו. אפשר לבקש לחשב נטו לחלופה מסוימת."
NOTHING_TO_CONFIRM = "אין כרגע פרטים שממתינים לאישור. במה אפשר לעזור?"
CANCELLED = "בסדר, הפסקתי את הבדיקה. אפשר להתחיל בדיקה חדשה בכל רגע."
UNCLEAR = 'ייתכן שלא הבנתי נכון. אפשר לנסח שוב, למשל: "אילו רכבים מתאימים לי?"'
HELP = (
    "בשיחה הזו אפשר לראות אילו רכבים מתאימים לקבוצת הזכאות שלך, לחשב נטו משוער לחלופות, "
    "ולהשוות אותן להסדר הנוכחי. במה נתחיל?"
)
GENERAL_FALLBACK = (
    "כרגע אין באפשרותי לנסח תשובה מפורטת לשאלה הזו. אפשר להמשיך בבדיקת החלופות, "
    'למשל: "אילו רכבים מתאימים לי?" או "כמה נטו עם ATTO 3?".'
)
ASK_WHICH_VEHICLE = "לאיזו חלופה התכוונת? אפשר לציין את שמה או את מספרה ברשימה."
BUDGET_EXCEEDED = "הבקשה מורכבת מדי לטיפול בבת אחת. אפשר לפצל אותה לשאלות קטנות יותר?"
LLM_RATE_LIMITED = "יש עומס זמני על המערכת. אפשר לנסות שוב בעוד רגע."
LLM_QUOTA_EXHAUSTED = (
    "העוזר החכם אינו זמין כרגע. אפשר להמשיך במסכי הקטלוג וההשוואות, או לנסות מאוחר יותר."
)
LLM_UNAVAILABLE = "יש תקלה זמנית בהבנת ההודעה. אפשר לנסות שוב?"
SAME_AS_BEFORE_IGNORED = "התשובה הזו כבר טופלה."

_ERRORS = {
    ErrorCode.NOT_FOUND: "לא מצאתי את המידע המבוקש.",
    ErrorCode.VERSION_CONFLICT: "הנתונים עודכנו בינתיים. טענתי אותם מחדש; אפשר לאשר שוב את השינוי?",
    ErrorCode.OPERATION_PAYLOAD_MISMATCH: "הבקשה התנגשה בבקשה קודמת. אפשר לנסות שוב?",
    ErrorCode.INVALID_STATE_TRANSITION: "לא ניתן לבצע את הפעולה בשלב הזה של השיחה.",
    ErrorCode.INVALID_BUSINESS_INPUT: "אחד הערכים אינו תקין. אפשר לבדוק אותו ולכתוב שוב?",
    ErrorCode.INPUT_OUT_OF_SUPPORTED_RANGE: "אחד הערכים מחוץ לטווח שהמערכת תומכת בו.",
    ErrorCode.GROSS_UP_NOT_CONVERGED: "לא ניתן היה להשלים את חישוב החזר החשמל לרכב הזה.",
    ErrorCode.RATE_LIMITED: LLM_RATE_LIMITED,
    ErrorCode.UNAUTHENTICATED: "צריך להתחבר מחדש כדי להמשיך.",
    ErrorCode.FORBIDDEN: "אין הרשאה לבצע את הפעולה הזו.",
}
GENERIC_ERROR = "יש תקלה זמנית, אפשר לנסות שוב."


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
    return f'מצאתי כמה רכבים שמתאימים ל"{name}":\n{lines}\nלאיזה מהם התכוונת?'


def vehicle_not_in_catalogue_text(name: str) -> str:
    return (
        f'לא מצאתי רכב בשם "{name}" בקטלוג הרכבים של החברה. '
        'אפשר לבדוק את הכתיב, או לבקש "אילו רכבים מתאימים לי?" כדי לראות את הרשימה.'
    )


def vehicle_not_eligible_text(labels: list[str]) -> str:
    names = ", ".join(labels)
    return (
        f"{names} נמצא בקטלוג הרכבים של החברה, אבל לפי הפרטים השמורים בפרופיל שלך הוא אינו "
        'בין החלופות שמתאימות לך. אפשר לבקש "אילו רכבים מתאימים לי?" כדי לראות את הרשימה, '
        "ואם אחד הפרטים בפרופיל אינו נכון, אפשר לתקן אותו כאן בשיחה."
    )


def vehicle_eligibility_unknown_text(labels: list[str]) -> str:
    names = ", ".join(labels)
    return f"{names} נמצא בקטלוג, אבל כדי לדעת אם הוא מתאים לך חסר עוד מידע."


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
    lines = ["כך נאסף המידע שאני משתמש בו:"]
    for label, names in by_source.items():
        lines.append(f"• {', '.join(names)}: מהפרופיל השמור שלך. מקור: {label}.")
    if missing:
        lines.append(f"• עדיין חסרים בפרופיל: {', '.join(missing)}.")
    lines.append(
        "• רשימת הרכבים והזכאות: קטלוג הרכבים וכללי הזכאות של החברה במערכת. "
        "שווי השימוש: נתוני רשות המסים."
    )
    lines.append(
        "הפרטים האישיים כפי שדווחו, וייתכן שחלקם אינם מעודכנים. אפשר לראות אותם במסך "
        '"הנתונים שלי", ולתקן כל פרט כאן בשיחה.'
    )
    return "\n".join(lines)


# --- The transportation representative ---------------------------------------------------------

REPRESENTATIVE_HELP = (
    "נציג התחבורה עוזר כשחסר פרט שאי אפשר לדעת מהפרופיל: קבוצת הזכאות שלך, הרכב שיש לך היום "
    "או סוג ההנעה שלו. שאלה על דמי ההשתתפות של רכב מסוים אפשר לשלוח מכפתור "
    '"שאלה לנציג" במסך ההשוואה. איזה פרט לברר מול הנציג?'
)
REPRESENTATIVE_HELP_AGAIN = (
    "כדי לשלוח שאלה לנציג צריך לדעת איזה פרט חסר, למשל: "
    '"לא ידועה לי קבוצת הזכאות שלי". אפשר גם להמשיך בינתיים, למשל: "אילו רכבים מתאימים לי?"'
)
