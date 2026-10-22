import {HttpClient} from '@angular/common/http';
import {Injectable, inject} from '@angular/core';
import {firstValueFrom} from 'rxjs';
import {API_BASE_URL, newId} from './api-config';
import type {
  AnswerSubmit,
  AssignmentFilter,
  ClaimResult,
  AnswerView,
  CaseContext,
  ComparisonDetails,
  ComparisonResult,
  CreateQuestionResult,
  EmployeeProfileView,
  EnvelopeOf,
  ListOptionsResult,
  MyRequestView,
  OptionCategory,
  RepresentativeFieldCode,
  RepresentativeRequestView,
  RepresentativeSummary,
} from './types';

/**
 * Business routes (contracts/openapi/business-api.json). Every amount, eligibility decision
 * and calculation comes from these responses; the browser only displays them.
 */
@Injectable({providedIn: 'root'})
export class BusinessApi {
  private readonly http = inject(HttpClient);
  private readonly base = inject(API_BASE_URL);

  profile(): Promise<EnvelopeOf<EmployeeProfileView>> {
    return firstValueFrom(this.http.get<EnvelopeOf<EmployeeProfileView>>(`${this.base}/me/profile`));
  }

  caseContext(caseId: string): Promise<EnvelopeOf<CaseContext>> {
    return firstValueFrom(this.http.get<EnvelopeOf<CaseContext>>(`${this.base}/cases/${caseId}`));
  }

  listOptions(caseId: string, categories: OptionCategory[]): Promise<EnvelopeOf<ListOptionsResult>> {
    return firstValueFrom(
      this.http.post<EnvelopeOf<ListOptionsResult>>(`${this.base}/cases/${caseId}/option-lists`, {categories}),
    );
  }

  compare(
    context: CaseContext,
    optionKeys: string[],
    includeCurrent: boolean,
    operationId = newId(),
  ): Promise<EnvelopeOf<ComparisonResult>> {
    return firstValueFrom(
      this.http.post<EnvelopeOf<ComparisonResult>>(`${this.base}/cases/${context.caseId}/comparisons`, {
        selection: {mode: 'explicit', optionKeys},
        includeCurrent,
        kind: 'regular',
        expectedCaseVersion: context.caseVersion,
        expectedProfileVersion: context.profile.profileVersion,
        operationId,
      }),
    );
  }

  comparisonDetails(caseId: string, runId: string): Promise<EnvelopeOf<ComparisonDetails>> {
    return firstValueFrom(
      this.http.get<EnvelopeOf<ComparisonDetails>>(`${this.base}/cases/${caseId}/comparisons/${runId}`),
    );
  }

  askRepresentative(
    context: CaseContext,
    fieldCode: RepresentativeFieldCode,
    optionKey: string | null,
    questionText: string,
    source: 'chat' | 'form' = 'chat',
  ): Promise<EnvelopeOf<CreateQuestionResult>> {
    return firstValueFrom(
      this.http.post<EnvelopeOf<CreateQuestionResult>>(`${this.base}/cases/${context.caseId}/representative-requests`, {
        fieldCode,
        optionKey,
        questionText,
        idempotencyKey: newId(),
        expectedCaseVersion: context.caseVersion,
        source,
      }),
    );
  }

  myRequests(): Promise<EnvelopeOf<MyRequestView[]>> {
    return firstValueFrom(this.http.get<EnvelopeOf<MyRequestView[]>>(`${this.base}/me/representative-requests`));
  }

  // --- representative role ----------------------------------------------------------------

  representativeRequests(
    statuses: string[],
    filters: {assignment?: AssignmentFilter; fieldCode?: string | null; search?: string | null} = {},
  ): Promise<EnvelopeOf<RepresentativeRequestView[]>> {
    const params = statuses.map(s => `status=${encodeURIComponent(s)}`);
    if (filters.assignment) params.push(`assignment=${filters.assignment}`);
    if (filters.fieldCode) params.push(`fieldCode=${encodeURIComponent(filters.fieldCode)}`);
    if (filters.search) params.push(`search=${encodeURIComponent(filters.search.slice(0, 100))}`);
    const query = params.join('&');
    return firstValueFrom(
      this.http.get<EnvelopeOf<RepresentativeRequestView[]>>(`${this.base}/representative-requests?${query}`),
    );
  }

  representativeRequest(requestId: string): Promise<EnvelopeOf<RepresentativeRequestView>> {
    return firstValueFrom(
      this.http.get<EnvelopeOf<RepresentativeRequestView>>(`${this.base}/representative-requests/${requestId}`),
    );
  }

  representativeSummary(): Promise<EnvelopeOf<RepresentativeSummary>> {
    return firstValueFrom(
      this.http.get<EnvelopeOf<RepresentativeSummary>>(`${this.base}/representative-requests/summary`),
    );
  }

  claim(requestId: string, expectedRequestVersion: number, operationId = newId()): Promise<EnvelopeOf<ClaimResult>> {
    return firstValueFrom(
      this.http.post<EnvelopeOf<ClaimResult>>(`${this.base}/representative-requests/${requestId}/claim`, {
        expectedRequestVersion,
        operationId,
      }),
    );
  }

  release(requestId: string, expectedAssignmentVersion: number, operationId = newId()): Promise<EnvelopeOf<unknown>> {
    return firstValueFrom(
      this.http.post<EnvelopeOf<unknown>>(`${this.base}/representative-requests/${requestId}/release`, {
        expectedAssignmentVersion,
        operationId,
      }),
    );
  }

  answer(requestId: string, body: AnswerSubmit): Promise<EnvelopeOf<AnswerView>> {
    return firstValueFrom(
      this.http.post<EnvelopeOf<AnswerView>>(`${this.base}/representative-requests/${requestId}/answers`, body),
    );
  }
}
