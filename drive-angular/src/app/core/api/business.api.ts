import {HttpClient} from '@angular/common/http';
import {Injectable, inject} from '@angular/core';
import {firstValueFrom} from 'rxjs';
import {API_BASE_URL, newId} from './api-config';
import type {
  AnswerSubmit,
  AnswerView,
  CaseContext,
  ComparisonDetails,
  ComparisonResult,
  CreateQuestionResult,
  EmployeeProfileView,
  EnvelopeOf,
  ListOptionsResult,
  OptionCategory,
  RepresentativeFieldCode,
  RepresentativeRequestView,
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
  ): Promise<EnvelopeOf<CreateQuestionResult>> {
    return firstValueFrom(
      this.http.post<EnvelopeOf<CreateQuestionResult>>(`${this.base}/cases/${context.caseId}/representative-requests`, {
        fieldCode,
        optionKey,
        questionText,
        idempotencyKey: newId(),
        expectedCaseVersion: context.caseVersion,
      }),
    );
  }

  // --- representative role ----------------------------------------------------------------

  representativeRequests(statuses: string[]): Promise<EnvelopeOf<RepresentativeRequestView[]>> {
    const query = statuses.map(s => `status=${encodeURIComponent(s)}`).join('&');
    return firstValueFrom(
      this.http.get<EnvelopeOf<RepresentativeRequestView[]>>(`${this.base}/representative-requests?${query}`),
    );
  }

  representativeRequest(requestId: string): Promise<EnvelopeOf<RepresentativeRequestView>> {
    return firstValueFrom(
      this.http.get<EnvelopeOf<RepresentativeRequestView>>(`${this.base}/representative-requests/${requestId}`),
    );
  }

  answer(requestId: string, body: AnswerSubmit): Promise<EnvelopeOf<AnswerView>> {
    return firstValueFrom(
      this.http.post<EnvelopeOf<AnswerView>>(`${this.base}/representative-requests/${requestId}/answers`, body),
    );
  }
}
