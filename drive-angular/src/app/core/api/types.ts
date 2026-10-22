/**
 * Contract types for the Angular client, taken only from the generated OpenAPI types
 * (`npm run api:types` from contracts/openapi/*.json). No hand-written DTOs.
 */
import type {components as Business} from './generated/business';
import type {components as Ai} from './generated/ai';

type B = Business['schemas'];
type A = Ai['schemas'];

export type ActorView = B['ActorView'];
export type SessionResponse = B['SessionResponse'];
export type EmployeeProfileView = B['EmployeeProfileView'];
export type CaseContext = B['CaseContext'];
export type OptionItem = B['OptionItem'];
export type OptionCategory = B['OptionCategory'];
export type ListOptionsResult = B['ListOptionsResult'];
export type ComparisonResult = B['ComparisonResult'];
export type ComparisonDetails = B['ComparisonDetails'];
export type ScenarioResult = B['ScenarioResult'];
export type BaselineResult = B['BaselineResult'];
export type CalculationBreakdown = B['CalculationBreakdown'];
export type MoneyAmount = B['MoneyAmount'];
export type Issue = B['Issue'];
export type EligibilityReason = B['EligibilityReason'];
export type Propulsion = B['Propulsion'];
export type CreateQuestionResult = B['CreateQuestionResult'];
/**
 * Claiming fields (contract change on develop, `feature/representative-claiming`). Added by hand
 * until `npm run api:types` is re-run with the generator installed.
 */
export type HandlingStatus = 'unclaimed' | 'assigned_to_me' | 'assigned_to_other' | 'completed';
export type RepresentativeRequestView = Omit<B['RepresentativeRequestView'], 'fieldCode' | 'expectedValueType'> & {
  fieldCode: RepresentativeFieldCode;
  expectedValueType: B['RepresentativeRequestView']['expectedValueType'] | 'text';
  assignmentVersion?: number | null;
  canAnswer?: boolean;
  canClaim?: boolean;
  canRelease?: boolean;
  claimedAt?: string | null;
  handlerDisplayName?: string | null;
  handlingStatus?: HandlingStatus | null;
  updatedAt?: string | null;
  employee?: {
    displayName?: string | null;
    employeeCode?: string | null;
    occupation?: string | null;
    currentVehicle?: string | null;
  } | null;
};
export interface RepresentativeSummary {
  total: number;
  unclaimed: number;
  assignedToMe: number;
  assignedToOthers: number;
  answered: number;
  cannotDetermine: number;
  cancelled: number;
}
export interface ClaimResult {
  assignmentId: string;
  assignmentVersion: number;
  claimedAt: string;
}
export type AssignmentFilter = 'all' | 'unclaimed' | 'mine' | 'others';
export type RepresentativeFieldCode = B['RepresentativeFieldCode'] | 'general.question';
export type AnswerSubmit = B['AnswerSubmit'];
export type AnswerView = B['AnswerView'];
export type RunSummary = B['RunSummary'];
export type ErrorCode = B['ErrorCode'];

export type EnvelopeOf<T> = {
  status: B['EnvelopeStatus'];
  data?: T | null;
  issues?: Issue[];
  caseVersion?: number | null;
  profileVersion?: number | null;
  traceId: string;
};

export type ConversationSummary = A['ConversationSummary'];
export type ConversationDetail = A['ConversationDetail'];
export type MessageView = A['MessageView'];
export type MessagePage = A['MessagePage'];
export type SubmitMessageResponse = A['SubmitMessageResponse'];
export type InputStatus = A['InputStatus'];

/** Input states after which polling stops (`InputStatus.status`). */
export const FINAL_INPUT_STATES = new Set(['completed', 'dead', 'needs_reconciliation']);
