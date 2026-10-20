"""Payslip-import errors. Each carries a stable payslip code, the shared `ErrorCode` used by the
HTTP boundary, and a natural Hebrew message. Messages never contain salary values, file names,
server paths or document text."""

from enum import StrEnum

from app.contracts.common import ErrorCode


class PayslipErrorCode(StrEnum):
    EMPTY_FILE = "EMPTY_FILE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    NOT_A_PDF = "NOT_A_PDF"
    MALFORMED_PDF = "MALFORMED_PDF"
    ENCRYPTED_PDF = "ENCRYPTED_PDF"
    TOO_MANY_PAGES = "TOO_MANY_PAGES"
    TOO_MUCH_TEXT = "TOO_MUCH_TEXT"
    SCANNED_UNSUPPORTED = "SCANNED_UNSUPPORTED"
    EXTRACTION_TIMEOUT = "EXTRACTION_TIMEOUT"
    CONVERSATION_NOT_FOUND = "CONVERSATION_NOT_FOUND"
    FORBIDDEN = "FORBIDDEN"
    DRAFT_NOT_FOUND = "DRAFT_NOT_FOUND"
    DRAFT_EXPIRED = "DRAFT_EXPIRED"
    DRAFT_NOT_EDITABLE = "DRAFT_NOT_EDITABLE"
    DRAFT_ALREADY_APPLIED = "DRAFT_ALREADY_APPLIED"
    DRAFT_VERSION_CONFLICT = "DRAFT_VERSION_CONFLICT"
    TOO_MANY_DRAFTS = "TOO_MANY_DRAFTS"
    FIELD_NOT_IN_DRAFT = "FIELD_NOT_IN_DRAFT"
    INVALID_SELECTION = "INVALID_SELECTION"
    FIELD_NOT_APPROVABLE = "FIELD_NOT_APPROVABLE"
    INVALID_FIELD_VALUE = "INVALID_FIELD_VALUE"
    OPERATION_PAYLOAD_MISMATCH = "OPERATION_PAYLOAD_MISMATCH"
    PROFILE_VERSION_CONFLICT = "PROFILE_VERSION_CONFLICT"
    BUSINESS_REJECTED = "BUSINESS_REJECTED"
    BUSINESS_UNAVAILABLE = "BUSINESS_UNAVAILABLE"


ERROR_CODES: dict[PayslipErrorCode, ErrorCode] = {
    PayslipErrorCode.EMPTY_FILE: ErrorCode.MALFORMED_REQUEST,
    PayslipErrorCode.FILE_TOO_LARGE: ErrorCode.INPUT_OUT_OF_SUPPORTED_RANGE,
    PayslipErrorCode.NOT_A_PDF: ErrorCode.MALFORMED_REQUEST,
    PayslipErrorCode.MALFORMED_PDF: ErrorCode.MALFORMED_REQUEST,
    PayslipErrorCode.ENCRYPTED_PDF: ErrorCode.INPUT_OUT_OF_SUPPORTED_RANGE,
    PayslipErrorCode.TOO_MANY_PAGES: ErrorCode.INPUT_OUT_OF_SUPPORTED_RANGE,
    PayslipErrorCode.TOO_MUCH_TEXT: ErrorCode.INPUT_OUT_OF_SUPPORTED_RANGE,
    PayslipErrorCode.SCANNED_UNSUPPORTED: ErrorCode.INPUT_OUT_OF_SUPPORTED_RANGE,
    PayslipErrorCode.EXTRACTION_TIMEOUT: ErrorCode.INFRA_UNAVAILABLE,
    PayslipErrorCode.CONVERSATION_NOT_FOUND: ErrorCode.NOT_FOUND,
    PayslipErrorCode.FORBIDDEN: ErrorCode.FORBIDDEN,
    PayslipErrorCode.DRAFT_NOT_FOUND: ErrorCode.NOT_FOUND,
    PayslipErrorCode.DRAFT_EXPIRED: ErrorCode.NOT_FOUND,
    PayslipErrorCode.DRAFT_NOT_EDITABLE: ErrorCode.INVALID_STATE_TRANSITION,
    PayslipErrorCode.DRAFT_ALREADY_APPLIED: ErrorCode.INVALID_STATE_TRANSITION,
    PayslipErrorCode.DRAFT_VERSION_CONFLICT: ErrorCode.VERSION_CONFLICT,
    PayslipErrorCode.TOO_MANY_DRAFTS: ErrorCode.RATE_LIMITED,
    PayslipErrorCode.FIELD_NOT_IN_DRAFT: ErrorCode.MALFORMED_REQUEST,
    PayslipErrorCode.INVALID_SELECTION: ErrorCode.MALFORMED_REQUEST,
    PayslipErrorCode.FIELD_NOT_APPROVABLE: ErrorCode.INVALID_BUSINESS_INPUT,
    PayslipErrorCode.INVALID_FIELD_VALUE: ErrorCode.INVALID_BUSINESS_INPUT,
    PayslipErrorCode.OPERATION_PAYLOAD_MISMATCH: ErrorCode.OPERATION_PAYLOAD_MISMATCH,
    PayslipErrorCode.PROFILE_VERSION_CONFLICT: ErrorCode.VERSION_CONFLICT,
    PayslipErrorCode.BUSINESS_REJECTED: ErrorCode.INVALID_BUSINESS_INPUT,
    PayslipErrorCode.BUSINESS_UNAVAILABLE: ErrorCode.INFRA_UNAVAILABLE,
}

MESSAGES_HE: dict[PayslipErrorCode, str] = {
    PayslipErrorCode.EMPTY_FILE: "הקובץ שהתקבל ריק. אפשר לנסות להעלות את התלוש שוב.",
    PayslipErrorCode.FILE_TOO_LARGE: "הקובץ גדול מדי. אפשר להעלות תלוש PDF בגודל של עד 2MB.",
    PayslipErrorCode.NOT_A_PDF: "הקובץ אינו PDF. כרגע אפשר להעלות רק תלוש שכר בפורמט PDF.",
    PayslipErrorCode.MALFORMED_PDF: "לא הצלחתי לקרוא את קובץ ה-PDF. ייתכן שהוא פגום.",
    PayslipErrorCode.ENCRYPTED_PDF: (
        "הקובץ מוגן בסיסמה. אפשר לשמור עותק ללא סיסמה ולהעלות אותו, או להזין את הנתונים ידנית."
    ),
    PayslipErrorCode.TOO_MANY_PAGES: "הקובץ ארוך מדי. אפשר להעלות תלוש של עד 5 עמודים.",
    PayslipErrorCode.TOO_MUCH_TEXT: "הקובץ מכיל יותר מדי טקסט מכדי שיהיה תלוש שכר בודד.",
    PayslipErrorCode.SCANNED_UNSUPPORTED: (
        "נראה שזה תלוש סרוק (תמונה), ואני לא יכול לקרוא אותו באופן אמין. "
        "אפשר להעלות PDF דיגיטלי או להזין את נתוני השכר ידנית."
    ),
    PayslipErrorCode.EXTRACTION_TIMEOUT: "קריאת התלוש ארכה יותר מדי. אפשר לנסות שוב.",
    PayslipErrorCode.CONVERSATION_NOT_FOUND: "השיחה לא נמצאה.",
    PayslipErrorCode.FORBIDDEN: "הפעולה אינה זמינה עבורך.",
    PayslipErrorCode.DRAFT_NOT_FOUND: ("הטיוטה אינה זמינה עוד, אפשר להעלות את התלוש מחדש."),
    PayslipErrorCode.DRAFT_EXPIRED: "תוקף הטיוטה פג, אפשר להעלות את התלוש מחדש.",
    PayslipErrorCode.DRAFT_NOT_EDITABLE: "אי אפשר לשנות טיוטה שכבר נשלחה לעדכון.",
    PayslipErrorCode.DRAFT_ALREADY_APPLIED: "הנתונים מהתלוש הזה כבר עודכנו בפרופיל.",
    PayslipErrorCode.DRAFT_VERSION_CONFLICT: (
        "הטיוטה השתנתה בינתיים. טוען אותה מחדש כדי שתוכל לבדוק את הערכים העדכניים."
    ),
    PayslipErrorCode.TOO_MANY_DRAFTS: (
        "יש כבר כמה תלושים שממתינים לבדיקה. אפשר לסיים או לבטל אחד מהם ולנסות שוב."
    ),
    PayslipErrorCode.FIELD_NOT_IN_DRAFT: "אחד השדות שנבחרו אינו חלק מהטיוטה.",
    PayslipErrorCode.INVALID_SELECTION: "יש לבחור לפחות שדה אחד, וכל שדה פעם אחת בלבד.",
    PayslipErrorCode.FIELD_NOT_APPROVABLE: (
        "יש שדה שחסר בו ערך או שהוא מסומן כלא ודאי. אפשר לתקן אותו או להסיר אותו מהאישור."
    ),
    PayslipErrorCode.INVALID_FIELD_VALUE: "אחד הערכים אינו תקין. אפשר לבדוק ולתקן אותו.",
    PayslipErrorCode.OPERATION_PAYLOAD_MISMATCH: (
        "בקשת האישור הזו כבר נשלחה עם ערכים אחרים. אפשר לרענן ולנסות שוב."
    ),
    PayslipErrorCode.PROFILE_VERSION_CONFLICT: (
        "הפרופיל עודכן בינתיים. טוען אותו מחדש כדי שתוכל לבדוק לפני האישור."
    ),
    PayslipErrorCode.BUSINESS_REJECTED: "לא ניתן היה לעדכן את הפרופיל עם הערכים האלה.",
    PayslipErrorCode.BUSINESS_UNAVAILABLE: (
        "לא הצלחתי לעדכן את הפרופיל כרגע. הטיוטה נשמרה, ואפשר לנסות שוב בעוד רגע."
    ),
}


class PayslipError(Exception):
    """A safe, expected payslip-import failure. `field` names a FieldPath value, never data."""

    def __init__(self, code: PayslipErrorCode, *, field: str | None = None) -> None:
        super().__init__(code.value)
        self.code = code
        self.field = field

    @property
    def error_code(self) -> ErrorCode:
        return ERROR_CODES[self.code]

    @property
    def message_he(self) -> str:
        return MESSAGES_HE[self.code]
