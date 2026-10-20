import {PayslipDraft, PayslipDraftField} from './payslip.models';

/** Test-only builders for payslip drafts (not imported by production code). */
export function field(overrides: Partial<PayslipDraftField> & Pick<PayslipDraftField, 'field' | 'status'>): PayslipDraftField {
  return {value: null, confidence: 'medium', evidence: [], alternatives: [], issues: [], ...overrides};
}

export function draft(overrides: Partial<PayslipDraft> = {}): PayslipDraft {
  return {
    draftId: 'd-1',
    conversationId: 'c-1',
    version: 1,
    status: 'review',
    mode: 'rules_only',
    pageCount: 1,
    createdAt: '2026-10-20T09:00:00Z',
    expiresAt: '2026-10-21T09:00:00Z',
    documentIssues: [],
    lastError: null,
    fields: [
      field({field: 'payroll.baseGross', status: 'extracted', value: '18500.00', evidence: [{page: 1, lineIndex: 6, text: '18,500.00 יסוד שכר'}]}),
      field({field: 'payroll.bonusGross', status: 'conflict', alternatives: ['1000.00', '2500.00']}),
      field({field: 'payroll.pensionEmployeeRate', status: 'needs_review', value: '0.06'}),
      field({field: 'payroll.creditPoints', status: 'missing'}),
    ],
    ...overrides,
  };
}

