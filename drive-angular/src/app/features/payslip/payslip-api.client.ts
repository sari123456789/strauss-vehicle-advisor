import {HttpClient, HttpEvent, HttpHeaders} from '@angular/common/http';
import {Injectable, inject} from '@angular/core';
import {Observable} from 'rxjs';
import {API_BASE_URL} from '../../core/api/api-config';
import {
  ApproveDraftRequest,
  CorrectDraftRequest,
  PayslipApprovalResult,
  PayslipDraft,
} from './payslip.models';

/**
 * Typed client for the payslip-import routes. Authentication (bearer token) is added by
 * `authInterceptor`; this client never handles credentials itself.
 */
@Injectable({providedIn: 'root'})
export class PayslipApiClient {
  private readonly http = inject(HttpClient);
  private readonly base = inject(API_BASE_URL);

  /** Raw PDF body; emits upload progress events and finally the created draft. */
  upload(conversationId: string, file: Blob): Observable<HttpEvent<PayslipDraft>> {
    return this.http.post<PayslipDraft>(
      `${this.base}/conversations/${encodeURIComponent(conversationId)}/payslip-imports`,
      file,
      {
        headers: new HttpHeaders({'Content-Type': 'application/pdf'}),
        observe: 'events',
        reportProgress: true,
      },
    );
  }

  get(draftId: string): Observable<PayslipDraft> {
    return this.http.get<PayslipDraft>(this.draftUrl(draftId));
  }

  correct(draftId: string, body: CorrectDraftRequest): Observable<PayslipDraft> {
    return this.http.patch<PayslipDraft>(this.draftUrl(draftId), body);
  }

  approve(draftId: string, body: ApproveDraftRequest): Observable<PayslipApprovalResult> {
    return this.http.post<PayslipApprovalResult>(`${this.draftUrl(draftId)}/approve`, body);
  }

  cancel(draftId: string): Observable<void> {
    return this.http.delete<void>(this.draftUrl(draftId));
  }

  private draftUrl(draftId: string): string {
    return `${this.base}/payslip-imports/${encodeURIComponent(draftId)}`;
  }
}
