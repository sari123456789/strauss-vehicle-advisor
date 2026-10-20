import {HttpErrorResponse} from '@angular/common/http';
import type {ErrorCode} from './types';

/**
 * Problem Details (`application/problem+json`, `code`) -> natural Hebrew text.
 * Raw HTTP statuses, codes and trace IDs are never shown to the user.
 */
const MESSAGES: Record<ErrorCode, string> = {
  MALFORMED_REQUEST: 'הבקשה לא הובנה. נסו שוב.',
  UNAUTHENTICATED: 'פג תוקף ההתחברות. יש להתחבר מחדש.',
  FORBIDDEN: 'אין לך הרשאה לפעולה הזו.',
  NOT_FOUND: 'המידע המבוקש לא נמצא.',
  VERSION_CONFLICT: 'הנתונים התעדכנו בינתיים. טוענים מחדש, ואפשר לנסות שוב.',
  OPERATION_PAYLOAD_MISMATCH: 'הפעולה כבר נשלחה עם פרטים אחרים. רעננו את המסך ונסו שוב.',
  INVALID_STATE_TRANSITION: 'הפעולה כבר בוצעה או שאינה אפשרית כרגע.',
  INVALID_BUSINESS_INPUT: 'אחד הערכים אינו תקין. בדקו את הפרטים ונסו שוב.',
  INPUT_OUT_OF_SUPPORTED_RANGE: 'אחד הערכים מחוץ לטווח שהמערכת תומכת בו.',
  GROSS_UP_NOT_CONVERGED: 'לא ניתן היה להשלים את החישוב עבור הנתונים האלה.',
  RATE_LIMITED: 'יש עומס רגעי. נסו שוב בעוד רגע.',
  INFRA_UNAVAILABLE: 'השירות אינו זמין כרגע. נסו שוב בעוד רגע.',
};

const FALLBACK = 'משהו השתבש. נסו שוב בעוד רגע.';
const OFFLINE = 'אין חיבור לשרת. בדקו את החיבור ונסו שוב.';

export function problemCode(error: unknown): ErrorCode | null {
  if (error instanceof HttpErrorResponse && error.error && typeof error.error === 'object') {
    const code = (error.error as {code?: unknown}).code;
    if (typeof code === 'string' && code in MESSAGES) return code as ErrorCode;
  }
  return null;
}

export function hebrewError(error: unknown): string {
  if (error instanceof HttpErrorResponse && error.status === 0) return OFFLINE;
  const code = problemCode(error);
  return code ? MESSAGES[code] : FALLBACK;
}

export function isRetryable(error: unknown): boolean {
  const code = problemCode(error);
  return code === 'INFRA_UNAVAILABLE' || code === 'RATE_LIMITED' || (error instanceof HttpErrorResponse && error.status === 0);
}
