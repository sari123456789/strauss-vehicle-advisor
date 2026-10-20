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
export type RepresentativeRequestView = B['RepresentativeRequestView'];
export type RepresentativeFieldCode = B['RepresentativeFieldCode'];
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
