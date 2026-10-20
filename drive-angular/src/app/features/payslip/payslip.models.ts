/**
 * Payslip-import DTOs, mirroring the PROVISIONAL backend DTOs in
 * `src/backend/app/api/routers/payslip_imports.py`. To be replaced by models generated from the
 * OpenAPI document once the contract is approved (avoid a competing frontend contract).
 *
 * Money and rates are decimal strings, never JS numbers. Rates are fractions: "0.06" = 6%.
 */

export type PayslipFieldPath =
  | 'payroll.baseGross'
  | 'payroll.commissionsGross'
  | 'payroll.bonusGross'
  | 'payroll.pensionEmployeeRate'
  | 'payroll.studyFundEmployeeRate'
  | 'payroll.creditPoints';

export type DraftFieldStatus =
  | 'extracted'
  | 'needs_review'
  | 'missing'
  | 'conflict'
  | 'rejected'
  | 'corrected';

export type DraftStatus = 'review' | 'applying' | 'applied';

export interface PayslipEvidence {
  page: number;
  lineIndex: number;
  text: string;
}

export interface PayslipDraftField {
  field: PayslipFieldPath;
  status: DraftFieldStatus;
  value: string | null;
  confidence: 'high' | 'medium' | 'low';
  evidence: PayslipEvidence[];
  alternatives: string[];
  issues: string[];
}

export interface PayslipDraft {
  draftId: string;
  conversationId: string;
  version: number;
  status: DraftStatus;
  mode: 'ai_assisted' | 'rules_only';
  pageCount: number;
  createdAt: string;
  expiresAt: string;
  fields: PayslipDraftField[];
  documentIssues: string[];
  lastError: string | null;
}

export interface PayslipCorrection {
  field: PayslipFieldPath;
  value: string;
}

export interface CorrectDraftRequest {
  expectedDraftVersion: number;
  corrections: PayslipCorrection[];
}

export interface ApproveDraftRequest {
  expectedDraftVersion: number;
  expectedProfileVersion: number;
  operationId: string;
  fields: PayslipFieldPath[];
}

/** Subset of the Business `UpdateInputsResult` the UI needs after approval. */
export interface PayslipApprovalResult {
  acceptedFields: string[];
  newCaseVersion: number;
  newProfileVersion: number | null;
  conflicts: unknown[];
  missingInformation: unknown[];
}

/** Problem Details body returned by the backend (only the safe fields we read). */
export interface PayslipProblem {
  code?: string;
  details?: {payslipCode?: string; field?: string};
}

export const APPROVABLE_STATUSES: ReadonlySet<DraftFieldStatus> = new Set([
  'extracted',
  'needs_review',
  'corrected',
]);

export const RATE_FIELDS: ReadonlySet<PayslipFieldPath> = new Set([
  'payroll.pensionEmployeeRate',
  'payroll.studyFundEmployeeRate',
]);

export const MONEY_FIELDS: ReadonlySet<PayslipFieldPath> = new Set([
  'payroll.baseGross',
  'payroll.commissionsGross',
  'payroll.bonusGross',
]);

export const MAX_PAYSLIP_BYTES = 2 * 1024 * 1024;
