import {Injectable, computed, inject, signal} from '@angular/core';
import {newId} from '../../../core/api/api-config';
import {BusinessApi} from '../../../core/api/business.api';
import {hebrewError} from '../../../core/api/problem';
import type {
  AnswerSubmit,
  AnswerView,
  AssignmentFilter,
  RepresentativeRequestView,
  RepresentativeSummary,
} from '../../../core/api/types';

export interface AnswerDraft {
  outcome: 'answered' | 'cannot_determine';
  value: string;
  effectiveFrom: string;
  effectiveTo: string;
  note: string;
}

/** Queue tabs: each maps to the server's `status` and `assignment` filters. */
export type QueueTab = 'all' | 'unclaimed' | 'mine' | 'others' | 'answered';

const TAB_QUERY: Record<QueueTab, {statuses: string[]; assignment: AssignmentFilter}> = {
  all: {statuses: ['open', 'answered', 'cannot_determine'], assignment: 'all'},
  unclaimed: {statuses: ['open'], assignment: 'unclaimed'},
  mine: {statuses: ['open'], assignment: 'mine'},
  others: {statuses: ['open'], assignment: 'others'},
  answered: {statuses: ['answered', 'cannot_determine'], assignment: 'all'},
};

/** How often the shared queue is refreshed while the page is open. */
const REFRESH_MS = 15_000;

/**
 * The shared representative queue (`/representative-requests`). Claiming follows the server
 * rules: an open request is claimed by one representative, who alone can answer or release it;
 * others see the handler's display name only. Every action carries an `operationId`, so a
 * network retry replays the stored result instead of acting twice.
 */
@Injectable({providedIn: 'root'})
export class InquiriesStore {
  private readonly api = inject(BusinessApi);

  readonly inquiries = signal<RepresentativeRequestView[]>([]);
  readonly summary = signal<RepresentativeSummary | null>(null);
  readonly tab = signal<QueueTab>('all');
  readonly fieldCode = signal<string | null>(null);
  readonly search = signal('');
  readonly selectedId = signal<string | null>(null);
  readonly lastAnswer = signal<AnswerView | null>(null);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  readonly refreshedAt = signal<Date | null>(null);
  readonly selected = computed(() => this.inquiries().find(q => q.requestId === this.selectedId()) ?? null);

  private operationId = newId();
  private timer: ReturnType<typeof setInterval> | null = null;

  async load(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      await this.refresh();
    } catch (error) {
      this.error.set(hebrewError(error));
    } finally {
      this.loading.set(false);
    }
  }

  /** Background refresh of the shared queue; errors stay silent until the next manual action. */
  watch(): void {
    this.unwatch();
    this.timer = setInterval(() => void this.refresh().catch(() => undefined), REFRESH_MS);
  }

  unwatch(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  setTab(tab: QueueTab): void {
    this.tab.set(tab);
    void this.load();
  }

  setFieldCode(fieldCode: string | null): void {
    this.fieldCode.set(fieldCode || null);
    void this.load();
  }

  setSearch(search: string): void {
    this.search.set(search);
    void this.load();
  }

  select(requestId: string | null): void {
    this.selectedId.set(requestId);
    this.lastAnswer.set(null);
    this.operationId = newId();
  }

  async claim(request: RepresentativeRequestView): Promise<boolean> {
    return this.act(async () => {
      await this.api.claim(request.requestId, request.requestVersion, this.operationId);
      this.selectedId.set(request.requestId);
    });
  }

  async release(request: RepresentativeRequestView): Promise<boolean> {
    if (request.assignmentVersion == null) return false;
    return this.act(() => this.api.release(request.requestId, request.assignmentVersion!, this.operationId));
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
    return this.act(async () => {
      const envelope = await this.api.answer(request.requestId, body);
      this.lastAnswer.set(envelope.data ?? null);
    });
  }

  private async act(action: () => Promise<unknown>): Promise<boolean> {
    this.loading.set(true);
    this.error.set(null);
    try {
      await action();
      this.operationId = newId();
      await this.refresh();
      return true;
    } catch (error) {
      this.error.set(hebrewError(error));
      await this.refresh().catch(() => undefined);
      return false;
    } finally {
      this.loading.set(false);
    }
  }

  private async refresh(): Promise<void> {
    const query = TAB_QUERY[this.tab()];
    const [list, summary] = await Promise.all([
      this.api.representativeRequests(query.statuses, {
        assignment: query.assignment,
        fieldCode: this.fieldCode(),
        search: this.search().trim() || null,
      }),
      this.api.representativeSummary(),
    ]);
    this.inquiries.set(list.data ?? []);
    this.summary.set(summary.data ?? null);
    this.refreshedAt.set(new Date());
  }
}
