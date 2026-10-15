/**
 * Display formatting for server decimal strings (e.g. "18500.00", "-1939").
 *
 * Formatting only: digits are regrouped exactly as received. The value is never converted
 * to a number, rounded, truncated or padded, so what the server calculated is what is shown.
 * Choosing which server field to display (e.g. netDisplay vs netRaw) is the caller's job.
 */
export interface DecimalFormatOptions {
  /** 'auto' shows "-" for negatives only; 'exceptZero' also shows "+" for positives (for differences). */
  signDisplay?: 'auto' | 'exceptZero';
  /** Append the shekel sign (default true). */
  currency?: boolean;
}

const DECIMAL_STRING = /^([+-])?(\d+)(?:\.(\d+))?$/;

export const MISSING_VALUE_DISPLAY = '—';

/** True when `value` is a plain decimal string the formatter accepts. */
export function isDecimalString(value: string): boolean {
  return DECIMAL_STRING.test(value);
}

/**
 * Formats a decimal string for display; null/undefined/empty render as "—" (missing is not zero).
 * Throws RangeError for anything that is not a plain decimal string (exponents, separators, NaN…).
 */
export function formatDecimalString(value: string | null | undefined, options: DecimalFormatOptions = {}): string {
  if (value === null || value === undefined || value === '') return MISSING_VALUE_DISPLAY;
  const match = DECIMAL_STRING.exec(value);
  if (!match) throw new RangeError(`Not a decimal string: ${JSON.stringify(value)}`);

  const [, sign, integerDigits, fractionDigits] = match;
  const integer = integerDigits.replace(/^0+(?=\d)/, '');
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const body = fractionDigits === undefined ? grouped : `${grouped}.${fractionDigits}`;

  const isZero = /^0+$/.test(integerDigits) && (fractionDigits === undefined || /^0+$/.test(fractionDigits));
  const isNegative = sign === '-' && !isZero;
  let prefix = '';
  if (isNegative) prefix = '-';
  else if (!isZero && options.signDisplay === 'exceptZero') prefix = '+';

  return options.currency === false ? `${prefix}${body}` : `${prefix}${body} ₪`;
}
