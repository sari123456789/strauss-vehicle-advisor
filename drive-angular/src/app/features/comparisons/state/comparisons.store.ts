import {Injectable, inject, signal} from '@angular/core';
import {BusinessApi} from '../../../core/api/business.api';
import {hebrewError} from '../../../core/api/problem';
import type {CaseContext, ComparisonDetails, ComparisonResult, ConversationSummary, RunSummary} from '../../../core/api/types';
import {ConversationStore} from '../../advisor/state/conversation.store';

export interface HistoryRow {
  conversation: ConversationSummary;
  run: RunSummary;
}

/**
 * Scenario calculations and their history, entirely from the Business API:
 * - `compare` runs `POST /cases/{id}/comparisons` for the selected options (with the current
 *   arrangement), using the case and profile versions just read (no stale write);
 * - `details` reads the immutable stored run (`GET /cases/{id}/comparisons/{runId}`);
 * - `history` lists the stored runs of every conversation's case (`recentRuns`, newest 20 per
 *   case) - saved comparisons are recovered from the server, never from the browser.
 */
@Injectable({providedIn: 'root'})
export class ComparisonsStore {
  private readonly api = inject(BusinessApi);
  private readonly conversations = inject(ConversationStore);

  readonly result = signal<ComparisonResult | null>(null);
  readonly caseId = signal<string | null>(null);
  readonly details = signal<ComparisonDetails | null>(null);
  readonly history = signal<HistoryRow[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  readonly notice = signal<string | null>(null);

  async compare(optionKeys: string[]): Promise<void> {
    if (!optionKeys.length) return;
    await this.run(async () => {
      const context = await this.context();
      const envelope = await this.api.compare(context, optionKeys, true);
      this.result.set(envelope.data ?? null);
      this.caseId.set(context.caseId);
    });
  }

  async loadDetails(caseId: string, runId: string): Promise<void> {
    await this.run(async () => {
      const envelope = await this.api.comparisonDetails(caseId, runId);
      this.details.set(envelope.data ?? null);
      this.caseId.set(caseId);
    });
  }

  async loadHistory(): Promise<void> {
    await this.run(async () => {
      await this.conversations.loadConversations();
      const rows: HistoryRow[] = [];
      for (const conversation of this.conversations.conversations()) {
        if (!conversation.caseId) continue;
        const envelope = await this.api.caseContext(conversation.caseId);
        for (const run of envelope.data?.recentRuns ?? []) rows.push({conversation, run});
      }
      rows.sort((a, b) => b.run.createdAt.localeCompare(a.run.createdAt));
      this.history.set(rows);
    });
  }

  private async context(): Promise<CaseContext> {
    const conversation = await this.conversations.ensureActive();
    if (!conversation.caseId) throw new Error('conversation without case');
    const envelope = await this.api.caseContext(conversation.caseId);
    if (!envelope.data) throw new Error('case context unavailable');
    return envelope.data;
  }

  private async run(action: () => Promise<void>): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    this.notice.set(null);
    try {
      await action();
    } catch (error) {
      this.error.set(hebrewError(error));
    } finally {
      this.loading.set(false);
    }
  }
}
