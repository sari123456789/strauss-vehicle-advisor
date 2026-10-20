import {Injectable, computed, inject, signal} from '@angular/core';
import {newId} from '../../../core/api/api-config';
import {BusinessApi} from '../../../core/api/business.api';
import {hebrewError} from '../../../core/api/problem';
import type {AnswerSubmit, AnswerView, RepresentativeRequestView} from '../../../core/api/types';

export interface AnswerDraft {
  outcome: 'answered' | 'cannot_determine';
  value: string;
  effectiveFrom: string;
  effectiveTo: string;
  note: string;
}

/**
 * The representative inbox (`/representative-requests`). Answers are structured
 * (`AnswerSubmit`): the value is sent as typed (money as a decimal string in ILS), never parsed
 * from free text; the note is shown to nobody as a number. The `operationId` is kept per
 * attempt so a network retry cannot answer twice.
 */
@Injectable({providedIn: 'root'})
export class InquiriesStore {
  private readonly api = inject(BusinessApi);

  readonly inquiries = signal<RepresentativeRequestView[]>([]);
  readonly selectedId = signal<string | null>(null);
  readonly lastAnswer = signal<AnswerView | null>(null);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  readonly openCount = computed(() => this.inquiries().filter(q => q.status === 'open').length);
  readonly selected = computed(() => this.inquiries().find(q => q.requestId === this.selectedId()) ?? null);

  private operationId = newId();

  async load(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      const envelope = await this.api.representativeRequests(['open', 'answered', 'cannot_determine']);
      this.inquiries.set(envelope.data ?? []);
      if (!this.selected()) this.selectedId.set(this.inquiries().find(q => q.status === 'open')?.requestId ?? null);
    } catch (error) {
      this.error.set(hebrewError(error));
    } finally {
      this.loading.set(false);
    }
  }

  select(requestId: string): void {
    this.selectedId.set(requestId);
    this.lastAnswer.set(null);
    this.operationId = newId();
  }

  async answer(request: RepresentativeRequestView, draft: AnswerDraft): Promise<boolean> {
    const body: AnswerSubmit = {
      operationId: this.operationId,
      expectedRequestVersion: request.requestVersion,
      outcome: draft.outcome,
      value: draft.outcome === 'answered' ? draft.value.trim() : null,
      unit: draft.outcome === 'answered' && request.expectedValueType === 'money' ? 'ILS' : null,
      effectiveFrom: draft.effectiveFrom || null,
      effectiveTo: draft.effectiveTo || null,
      note: draft.note.trim() || null,
    };
    this.loading.set(true);
    this.error.set(null);
    try {
      const envelope = await this.api.answer(request.requestId, body);
      this.lastAnswer.set(envelope.data ?? null);
      this.operationId = newId();
      await this.load();
      return true;
    } catch (error) {
      this.error.set(hebrewError(error));
      return false;
    } finally {
      this.loading.set(false);
    }
  }
}
