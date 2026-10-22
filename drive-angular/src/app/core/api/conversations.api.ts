import {HttpClient} from '@angular/common/http';
import {Injectable, inject} from '@angular/core';
import {firstValueFrom} from 'rxjs';
import {API_BASE_URL, currentMonth, newId} from './api-config';
import type {
  ConversationDetail,
  ConversationSummary,
  InputStatus,
  MessagePage,
  SubmitMessageResponse,
} from './types';

/** AI conversation routes (contracts/openapi/ai-api.json): replies are polled (decision P). */
@Injectable({providedIn: 'root'})
export class ConversationsApi {
  private readonly http = inject(HttpClient);
  private readonly base = `${inject(API_BASE_URL)}/conversations`;

  start(clientRequestId = newId(), calculationMonth = currentMonth()): Promise<ConversationSummary> {
    return firstValueFrom(this.http.post<ConversationSummary>(this.base, {clientRequestId, calculationMonth}));
  }

  async list(): Promise<ConversationSummary[]> {
    const page = await firstValueFrom(this.http.get<{conversations: ConversationSummary[]}>(this.base));
    return page.conversations;
  }

  get(conversationId: string): Promise<ConversationDetail> {
    return firstValueFrom(this.http.get<ConversationDetail>(`${this.base}/${conversationId}`));
  }

  /** Removes it from the employee's list; the server keeps its history. */
  remove(conversationId: string): Promise<void> {
    return firstValueFrom(this.http.delete<void>(`${this.base}/${conversationId}`));
  }

  send(conversationId: string, text: string, messageId = newId()): Promise<SubmitMessageResponse> {
    return firstValueFrom(
      this.http.post<SubmitMessageResponse>(`${this.base}/${conversationId}/messages`, {messageId, text}),
    );
  }

  messages(conversationId: string, afterSequence: number): Promise<MessagePage> {
    return firstValueFrom(
      this.http.get<MessagePage>(`${this.base}/${conversationId}/messages`, {
        params: {afterSequence: String(afterSequence)},
      }),
    );
  }

  input(conversationId: string, inputId: string): Promise<InputStatus> {
    return firstValueFrom(this.http.get<InputStatus>(`${this.base}/${conversationId}/inputs/${inputId}`));
  }
}
