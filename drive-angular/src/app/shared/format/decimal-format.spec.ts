import {formatDecimalString, isDecimalString} from './decimal-format';
import {MoneyPipe} from './money.pipe';

describe('formatDecimalString', () => {
  it('groups thousands and appends the shekel sign', () => {
    expect(formatDecimalString('11693')).toBe('11,693 ₪');
    expect(formatDecimalString('1000000')).toBe('1,000,000 ₪');
    expect(formatDecimalString('999')).toBe('999 ₪');
  });

  it('keeps fraction digits exactly as received (no rounding, trimming or padding)', () => {
    expect(formatDecimalString('11692.817')).toBe('11,692.817 ₪');
    expect(formatDecimalString('18500.00')).toBe('18,500.00 ₪');
    expect(formatDecimalString('0.000001')).toBe('0.000001 ₪');
    expect(formatDecimalString('1938.6809999999999999999')).toBe('1,938.6809999999999999999 ₪');
  });

  it('preserves precision beyond float range', () => {
    expect(formatDecimalString('12345678901234567890.123456789', {currency: false}))
      .toBe('12,345,678,901,234,567,890.123456789');
  });

  it('formats signs for differences', () => {
    expect(formatDecimalString('-1939', {signDisplay: 'exceptZero'})).toBe('-1,939 ₪');
    expect(formatDecimalString('722', {signDisplay: 'exceptZero'})).toBe('+722 ₪');
    expect(formatDecimalString('+722')).toBe('722 ₪');
    expect(formatDecimalString('0', {signDisplay: 'exceptZero'})).toBe('0 ₪');
    expect(formatDecimalString('-0.00')).toBe('0.00 ₪');
  });

  it('drops redundant leading zeros of the integer part only', () => {
    expect(formatDecimalString('007.50', {currency: false})).toBe('7.50');
    expect(formatDecimalString('000', {currency: false})).toBe('0');
  });

  it('renders missing values as a dash, never as zero', () => {
    expect(formatDecimalString(null)).toBe('—');
    expect(formatDecimalString(undefined)).toBe('—');
    expect(formatDecimalString('')).toBe('—');
  });

  it('rejects anything that is not a plain decimal string', () => {
    for (const bad of ['1e3', '1,000', ' 5', '5 ', 'NaN', 'Infinity', '.5', '5.', '--1', '₪5']) {
      expect(isDecimalString(bad)).toBe(false);
      expect(() => formatDecimalString(bad)).toThrow(RangeError);
    }
  });
});

describe('MoneyPipe', () => {
  const pipe = new MoneyPipe();

  it('formats with options', () => {
    expect(pipe.transform('722', 'exceptZero')).toBe('+722 ₪');
    expect(pipe.transform('2.25', 'auto', false)).toBe('2.25');
  });

  it('shows a dash instead of a guessed number for malformed input', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    expect(pipe.transform('12e3')).toBe('—');
    expect(spy).toHaveBeenCalledOnce();
    spy.mockRestore();
  });
});
