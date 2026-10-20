import {formatDecimalString} from '../../shared/format/decimal-format';
import {
  DraftFieldStatus,
  MAX_PAYSLIP_BYTES,
  MONEY_FIELDS,
  PayslipFieldPath,
  RATE_FIELDS,
} from './payslip.models';

/** Pure helpers: Hebrew labels, exact decimal-string display/conversion, input validation. */

export const FIELD_LABELS: Record<PayslipFieldPath, string> = {
  'payroll.baseGross': 'שכר בסיס חודשי',
  'payroll.commissionsGross': 'עמלות',
  'payroll.bonusGross': 'בונוס',
  'payroll.pensionEmployeeRate': 'הפרשת עובד לפנסיה',
  'payroll.studyFundEmployeeRate': 'הפרשת עובד לקרן השתלמות',
  'payroll.creditPoints': 'נקודות זיכוי',
};

export const STATUS_LABELS: Record<DraftFieldStatus, string> = {
  extracted: 'זוהה בתלוש',
  needs_review: 'לבדיקתך',
  conflict: 'נמצאו כמה ערכים',
  missing: 'לא נמצא בתלוש',
  rejected: 'לא ניתן להשתמש',
  corrected: 'עודכן על ידך',
};

export const ISSUE_LABELS: Partial<Record<string, string>> = {
  multiple_amounts_on_line: 'בשורה יש כמה מספרים, צריך לבחור את הנכון',
  multiple_values: 'נמצאו כמה ערכים שונים',
  employer_share_only: 'נמצא רק חלק המעסיק',
  cumulative_only: 'נמצא רק ערך מצטבר',
  rate_not_printed: 'בתלוש מופיע סכום בלבד, בלי אחוז — אפשר להזין את האחוז',
  negative_amount: 'הסכום שלילי',
  invalid_value: 'הערך אינו בטווח הנתמך',
  not_confirmed_by_ai: 'כדאי לוודא מול התלוש',
  ai_only: 'זוהה בלי תווית ברורה — כדאי לוודא',
  ai_unavailable: 'זוהה לפי כללים בלבד — כדאי לוודא',
  suspicious_content: 'בתלוש יש טקסט חריג — כדאי לוודא',
};

export const DOCUMENT_ISSUE_LABELS: Partial<Record<string, string>> = {
  suspicious_content: 'בקובץ נמצא טקסט חריג, ולכן כל הערכים מוצגים לבדיקתך.',
  no_candidates: 'לא נמצאו בתלוש נתוני שכר שאפשר לזהות.',
};

/** Moves the decimal point of a non-negative decimal string, exactly (no floats). */
export function shiftDecimal(value: string, places: number): string {
  const [whole, fraction = ''] = value.split('.');
  let digits = whole + fraction;
  let point = whole.length + places;
  if (point <= 0) {
    digits = '0'.repeat(1 - point) + digits;
    point = 1;
  }
  if (point > digits.length) {
    digits += '0'.repeat(point - digits.length);
  }
  const intPart = digits.slice(0, point).replace(/^0+(?=\d)/, '');
  const fracPart = digits.slice(point);
  return fracPart ? `${intPart}.${fracPart}` : intPart;
}

export function displayValue(field: PayslipFieldPath, value: string | null): string {
  if (value === null) return '—';
  if (RATE_FIELDS.has(field)) return `${shiftDecimal(value, 2)}%`;
  if (MONEY_FIELDS.has(field)) return formatDecimalString(value);
  return value;
}

/** Value shown in an edit box: rates as percent, others unchanged. */
export function editableValue(field: PayslipFieldPath, value: string | null): string {
  if (value === null) return '';
  return RATE_FIELDS.has(field) ? shiftDecimal(value, 2) : value;
}

const DECIMAL = /^\d+(\.\d+)?$/;
const RATE = /^(0(\.\d+)?|1(\.0+)?)$/;

export type ParsedInput = {ok: true; value: string} | {ok: false; message: string};

/**
 * Employee input -> contract value. Rates are typed as percent ("6" or "6.5%") and sent as
 * fractions ("0.06"). The server validates again; this only gives immediate feedback.
 */
export function parseUserInput(field: PayslipFieldPath, raw: string): ParsedInput {
  const cleaned = raw.replace(/[\s,₪%‎‏]/g, '');
  if (!DECIMAL.test(cleaned) || cleaned.length > 30) {
    return {ok: false, message: 'יש להזין מספר חיובי, למשל 18500 או 6.5'};
  }
  if (RATE_FIELDS.has(field)) {
    const fraction = shiftDecimal(cleaned, -2);
    return RATE.test(fraction) ? {ok: true, value: fraction} : {ok: false, message: 'יש להזין אחוז בין 0 ל-100'};
  }
  if (field === 'payroll.creditPoints' && cleaned.length > 10) {
    return {ok: false, message: 'מספר נקודות הזיכוי ארוך מדי'};
  }
  return {ok: true, value: cleaned};
}

export type FileCheck = {ok: true} | {ok: false; code: 'NOT_A_PDF' | 'FILE_TOO_LARGE' | 'EMPTY_FILE'};

export function checkPayslipFile(file: Pick<File, 'name' | 'type' | 'size'>): FileCheck {
  const isPdf = file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf');
  if (!isPdf) return {ok: false, code: 'NOT_A_PDF'};
  if (file.size === 0) return {ok: false, code: 'EMPTY_FILE'};
  if (file.size > MAX_PAYSLIP_BYTES) return {ok: false, code: 'FILE_TOO_LARGE'};
  return {ok: true};
}
