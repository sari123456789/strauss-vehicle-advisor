import {Injectable, computed, inject, signal} from '@angular/core';
import {BusinessApi} from '../../../core/api/business.api';
import {hebrewError} from '../../../core/api/problem';
import type {Issue, OptionItem} from '../../../core/api/types';
import {ConversationStore} from '../../advisor/state/conversation.store';

/**
 * Catalogue options of the active conversation's case (`POST /cases/{id}/option-lists`).
 * Eligibility, reasons and amounts are the Business result; the browser never decides them.
 */
@Injectable({providedIn: 'root'})
export class CatalogStore {
  private readonly api = inject(BusinessApi);
  private readonly conversations = inject(ConversationStore);

  readonly options = signal<OptionItem[]>([]);
  readonly missing = signal<Issue[]>([]);
  readonly selected = signal<string[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);
  readonly selectedOptions = computed(() => this.options().filter(o => this.selected().includes(o.optionKey)));

  async load(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      const conversation = await this.conversations.ensureActive();
      if (!conversation.caseId) throw new Error('conversation without case');
      const envelope = await this.api.listOptions(conversation.caseId, ['regular', 'family', 'waiver']);
      this.options.set(envelope.data?.items ?? []);
      this.missing.set(envelope.data?.missingInformation ?? []);
      const available = new Set(this.options().map(o => o.optionKey));
      this.selected.update(keys => keys.filter(k => available.has(k)));
    } catch (error) {
      this.error.set(hebrewError(error));
    } finally {
      this.loading.set(false);
    }
  }

  toggle(optionKey: string): void {
    this.selected.update(keys => (keys.includes(optionKey) ? keys.filter(k => k !== optionKey) : [...keys, optionKey]));
  }

  clear(): void {
    this.selected.set([]);
  }
}
