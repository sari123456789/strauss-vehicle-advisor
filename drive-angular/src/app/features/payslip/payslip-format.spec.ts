import {HttpErrorResponse} from '@angular/common/http';
import {toPayslipError} from './payslip-errors';
import {checkPayslipFile, displayValue, editableValue, parseUserInput, shiftDecimal} from './payslip-format';

describe('shiftDecimal', () => {
  it('moves the decimal point exactly, without floats', () => {
    expect(shiftDecimal('0.06', 2)).toBe('6');
    expect(shiftDecimal('0.0600', 2)).toBe('6.00');
    expect(shiftDecimal('0.025', 2)).toBe('2.5');
    expect(shiftDecimal('6', -2)).toBe('0.06');
    expect(shiftDecimal('6.5', -2)).toBe('0.065');
    expect(shiftDecimal('100', -2)).toBe('1.00');
    expect(shiftDecimal('0.1', -2)).toBe('0.001');
  });
});

describe('display and input', () => {
  it('shows rates as percent and money with the shekel sign', () => {
    expect(displayValue('payroll.pensionEmployeeRate', '0.06')).toBe('6%');
    expect(displayValue('payroll.baseGross', '18500.00')).toBe('18,500.00 ₪');
    expect(displayValue('payroll.creditPoints', '2.25')).toBe('2.25');
    expect(displayValue('payroll.bonusGross', null)).toBe('—');
    expect(editableValue('payroll.studyFundEmployeeRate', '0.025')).toBe('2.5');
  });

  it('converts typed percentages to contract fractions and cleans formatting', () => {
    expect(parseUserInput('payroll.pensionEmployeeRate', '6.5%')).toEqual({ok: true, value: '0.065'});
    expect(parseUserInput('payroll.baseGross', '₪ 18,500.00')).toEqual({ok: true, value: '18500.00'});
    expect(parseUserInput('payroll.creditPoints', '2.25')).toEqual({ok: true, value: '2.25'});
  });

  it('rejects invalid input before any request', () => {
    expect(parseUserInput('payroll.pensionEmployeeRate', '150').ok).toBe(false);
    expect(parseUserInput('payroll.baseGross', '-100').ok).toBe(false);
    expect(parseUserInput('payroll.baseGross', 'abc').ok).toBe(false);
    expect(parseUserInput('payroll.baseGross', '1e5').ok).toBe(false);
    expect(parseUserInput('payroll.creditPoints', '12345678901').ok).toBe(false);
  });
});

describe('checkPayslipFile', () => {
  it('accepts a PDF up to 2MB only', () => {
    expect(checkPayslipFile({name: 'p.pdf', type: 'application/pdf', size: 1000})).toEqual({ok: true});
    expect(checkPayslipFile({name: 'p.PDF', type: '', size: 1000})).toEqual({ok: true});
    expect(checkPayslipFile({name: 'p.png', type: 'image/png', size: 1000})).toEqual({ok: false, code: 'NOT_A_PDF'});
    expect(checkPayslipFile({name: 'p.pdf', type: 'application/pdf', size: 0})).toEqual({ok: false, code: 'EMPTY_FILE'});
    expect(checkPayslipFile({name: 'p.pdf', type: 'application/pdf', size: 3 * 1024 * 1024})).toEqual({
      ok: false,
      code: 'FILE_TOO_LARGE',
    });
  });
});

describe('toPayslipError', () => {
  it('maps payslip codes to Hebrew without HTTP statuses', () => {
    const error = toPayslipError(
      new HttpErrorResponse({status: 422, error: {code: 'INPUT_OUT_OF_SUPPORTED_RANGE', details: {payslipCode: 'SCANNED_UNSUPPORTED'}}}),
    );
    expect(error.code).toBe('SCANNED_UNSUPPORTED');
    expect(error.message).toContain('סרוק');
    expect(error.message).not.toMatch(/\d{3}/);
    expect(error.retryable).toBe(false);
  });

  it('treats network failures and unknown codes as retryable', () => {
    expect(toPayslipError(new HttpErrorResponse({status: 0})).code).toBe('NETWORK');
    expect(toPayslipError(new HttpErrorResponse({status: 401})).code).toBe('UNAUTHENTICATED');
    const unknown = toPayslipError(new HttpErrorResponse({status: 500, error: {details: {payslipCode: 'SOMETHING_NEW'}}}));
    expect(unknown.code).toBe('UNKNOWN');
    expect(unknown.retryable).toBe(true);
  });
});
