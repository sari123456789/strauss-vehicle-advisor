import {HttpErrorResponse} from '@angular/common/http';
import {PayslipProblem} from './payslip.models';

/**
 * Stable `details.payslipCode` values -> natural Hebrew text. Mirrors `MESSAGES_HE` in
 * `src/backend/app/ai/application/payslip/errors.py`. HTTP statuses are never shown.
 */
export const PAYSLIP_MESSAGES: Record<string, string> = {
  EMPTY_FILE: 'הקובץ שהתקבל ריק. אפשר לנסות להעלות את התלוש שוב.',
  FILE_TOO_LARGE: 'הקובץ גדול מדי. אפשר להעלות תלוש PDF בגודל של עד 2MB.',
  NOT_A_PDF: 'הקובץ אינו PDF. כרגע אפשר להעלות רק תלוש שכר בפורמט PDF.',
  MALFORMED_PDF: 'לא הצלחתי לקרוא את קובץ ה-PDF. ייתכן שהוא פגום.',
  ENCRYPTED_PDF: 'הקובץ מוגן בסיסמה. אפשר לשמור עותק ללא סיסמה ולהעלות אותו, או להזין את הנתונים ידנית.',
  TOO_MANY_PAGES: 'הקובץ ארוך מדי. אפשר להעלות תלוש של עד 5 עמודים.',
  TOO_MUCH_TEXT: 'הקובץ מכיל יותר מדי טקסט מכדי שיהיה תלוש שכר בודד.',
  SCANNED_UNSUPPORTED:
    'נראה שהתלוש הוא תמונה (סריקה או צילום מסך), ואי אפשר לקרוא אותו באופן אמין. אפשר להעלות את קובץ ה-PDF המקורי ממערכת השכר, או להזין את נתוני השכר ידנית.',
  EXTRACTION_TIMEOUT: 'קריאת התלוש ארכה יותר מדי. אפשר לנסות שוב.',
  CONVERSATION_NOT_FOUND: 'השיחה לא נמצאה.',
  FORBIDDEN: 'הפעולה אינה זמינה עבורך.',
  DRAFT_NOT_FOUND: 'הטיוטה אינה זמינה עוד, אפשר להעלות את התלוש מחדש.',
  DRAFT_EXPIRED: 'תוקף הטיוטה פג, אפשר להעלות את התלוש מחדש.',
  DRAFT_NOT_EDITABLE: 'אי אפשר לשנות טיוטה שכבר נשלחה לעדכון.',
  DRAFT_ALREADY_APPLIED: 'הנתונים מהתלוש הזה כבר עודכנו בפרופיל.',
  DRAFT_VERSION_CONFLICT: 'הטיוטה השתנתה בינתיים. טענתי אותה מחדש כדי שתוכלו לבדוק את הערכים העדכניים.',
  TOO_MANY_DRAFTS: 'יש כבר כמה תלושים שממתינים לבדיקה. אפשר לסיים או לבטל אחד מהם ולנסות שוב.',
  FIELD_NOT_IN_DRAFT: 'אחד השדות שנבחרו אינו חלק מהטיוטה.',
  INVALID_SELECTION: 'יש לבחור לפחות שדה אחד.',
  FIELD_NOT_APPROVABLE: 'יש שדה שחסר בו ערך או שהוא מסומן כלא ודאי. אפשר לתקן אותו או להסיר אותו מהאישור.',
  INVALID_FIELD_VALUE: 'אחד הערכים אינו תקין. אפשר לבדוק ולתקן אותו.',
  OPERATION_PAYLOAD_MISMATCH: 'בקשת האישור הזו כבר נשלחה עם ערכים אחרים. אפשר לרענן ולנסות שוב.',
  PROFILE_VERSION_CONFLICT: 'הפרופיל עודכן בינתיים. כדאי לרענן את הפרופיל ולבדוק לפני האישור.',
  BUSINESS_REJECTED: 'לא ניתן היה לעדכן את הפרופיל עם הערכים האלה.',
  BUSINESS_UNAVAILABLE: 'לא הצלחתי לעדכן את הפרופיל כרגע. הטיוטה נשמרה, ואפשר לנסות שוב בעוד רגע.',
  NETWORK: 'אין כרגע חיבור לשרת. אפשר לנסות שוב בעוד רגע.',
  UNAUTHENTICATED: 'פג תוקף ההתחברות. יש להתחבר מחדש ולנסות שוב.',
  UNKNOWN: 'משהו השתבש. אפשר לנסות שוב.',
};

const RETRYABLE = new Set(['EXTRACTION_TIMEOUT', 'BUSINESS_UNAVAILABLE', 'NETWORK', 'UNKNOWN']);
/** Codes after which the draft is gone and the employee must upload again. */
export const RESTART_CODES = new Set(['DRAFT_NOT_FOUND', 'DRAFT_EXPIRED']);

export interface PayslipUiError {
  code: string;
  field?: string;
  message: string;
  retryable: boolean;
}

export function toPayslipError(error: unknown): PayslipUiError {
  let code = 'UNKNOWN';
  let field: string | undefined;
  if (typeof error === 'string') {
    code = error;
  } else if (error instanceof HttpErrorResponse) {
    const body = (error.error ?? {}) as PayslipProblem;
    if (error.status === 0) code = 'NETWORK';
    else if (body.details?.payslipCode) code = body.details.payslipCode;
    else if (body.code === 'UNAUTHENTICATED' || error.status === 401) code = 'UNAUTHENTICATED';
    field = body.details?.field;
  }
  const known = code in PAYSLIP_MESSAGES ? code : 'UNKNOWN';
  return {code: known, field, message: PAYSLIP_MESSAGES[known], retryable: RETRYABLE.has(known)};
}
