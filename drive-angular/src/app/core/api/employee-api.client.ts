import {HttpClient} from '@angular/common/http';
import {Injectable, inject} from '@angular/core';
import {Observable, map, of, switchMap} from 'rxjs';
import {DemoStorage} from '../storage/demo-storage';
import {API_V1_BASE_URL} from './api-session';

/** Subset of the Business `EmployeeProfileView` (contracts/openapi/business-api.json). */
export interface ProfileFieldView {
  value: string | number | boolean | null;
  status: 'missing' | 'needs_clarification' | 'usable';
  source?: string | null;
  updatedAt?: string | null;
}

export interface EmployeeProfileView {
  profileVersion: number;
  baseGross: ProfileFieldView;
  commissionsGross: ProfileFieldView;
  bonusGross: ProfileFieldView;
  pensionEmployeeRate: ProfileFieldView;
  studyFundEmployeeRate: ProfileFieldView;
  creditPoints: ProfileFieldView;
}

interface Envelope<T> {
  status: 'ok' | 'partial' | 'needs_information' | 'error';
  data: T | null;
}

interface ConversationSummary {
  conversationId: string;
  caseId: string | null;
  status: string;
  calculationMonth: string;
}

const CONVERSATION_KEY = 'api.conversationRequest';

function currentMonth(now = new Date()): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

/** Real employee profile and the employee's working conversation (real API). */
@Injectable({providedIn: 'root'})
export class EmployeeApiClient {
  private readonly http = inject(HttpClient);
  private readonly base = inject(API_V1_BASE_URL);
  private readonly storage = inject(DemoStorage);

  profile(): Observable<EmployeeProfileView> {
    return this.http.get<Envelope<EmployeeProfileView>>(`${this.base}/me/profile`).pipe(
      map((envelope) => {
        if (!envelope.data) throw new Error('profile unavailable');
        return envelope.data;
      }),
    );
  }

  /**
   * The employee's active conversation (needed by tools such as the payslip import). Starting is
   * idempotent on `clientRequestId`, which is remembered per employee and month, so repeated
   * calls resume the same conversation and business case instead of creating new ones.
   */
  workingConversation(subjectId: string): Observable<string> {
    const month = currentMonth();
    const key = `${CONVERSATION_KEY}.${subjectId}.${month}`;
    const known = this.storage.read(key, '');
    const clientRequestId = known || crypto.randomUUID();
    if (!known) this.storage.save(key, clientRequestId);
    return of(clientRequestId).pipe(
      switchMap((id) =>
        this.http.post<ConversationSummary>(`${this.base}/conversations`, {
          clientRequestId: id,
          calculationMonth: month,
        }),
      ),
      map((conversation) => conversation.conversationId),
    );
  }
}
