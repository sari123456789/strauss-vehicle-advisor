import {Injectable, computed, inject, signal} from '@angular/core';
import {ConversationsApi} from '../../../core/api/conversations.api';
import {hebrewError} from '../../../core/api/problem';
import {FINAL_INPUT_STATES, type ConversationSummary, type MessageView} from '../../../core/api/types';

const POLL_MS = 2000;
const WATCH_MS = 5000;

interface PendingTurn {
  inputId: string;
  text: string;
  status: string;
}

/**
 * The employee's advisor conversation, backed by the AI conversation API.
 *
 * Sending is `202` + polling (decision P): while a turn is pending the store polls new
 * messages every 2 s and the input status until it is final. While the advisor screen is open
 * it also watches for asynchronous continuations (a representative answer) every 5 s. History
 * lives on the server: reopening a conversation reloads its full transcript.
 */
@Injectable({providedIn: 'root'})
export class ConversationStore {
  private readonly api = inject(ConversationsApi);

  readonly conversations = signal<ConversationSummary[]>([]);
  readonly active = signal<ConversationSummary | null>(null);
  readonly messages = signal<MessageView[]>([]);
  readonly pending = signal<PendingTurn | null>(null);
  readonly error = signal<string | null>(null);
  readonly loading = signal(false);
  /** A conversation is being created or opened: sending waits so nothing lands in the old one. */
  readonly switching = signal(false);
  readonly busy = computed(() => {
    const turn = this.pending();
    return this.switching() || (turn !== null && !FINAL_INPUT_STATES.has(turn.status));
  });
  /** A turn that ended without a reply (`dead` / `needs_reconciliation`): offer a resend. */
  readonly failedTurn = computed(() => {
    const turn = this.pending();
    return turn !== null && turn.status !== 'completed' && FINAL_INPUT_STATES.has(turn.status) ? turn : null;
  });

  private lastSequence = 0;
  private pollTimer: ReturnType<typeof setTimeout> | null = null;
  private watchTimer: ReturnType<typeof setInterval> | null = null;
  /** Bumped by every switch of conversation; late results of an earlier switch are dropped. */
  private generation = 0;

  async loadConversations(): Promise<void> {
    try {
      this.conversations.set(await this.api.list());
    } catch (error) {
      this.error.set(hebrewError(error));
    }
  }

  /** The active conversation; reopens the most recent one or starts a new one. */
  async ensureActive(): Promise<ConversationSummary> {
    const current = this.active();
    if (current) return current;
    const generation = this.generation;
    await this.loadConversations();
    const switched = this.active();
    if (generation !== this.generation && switched) return switched; // the user chose meanwhile
    const latest = this.conversations()[0];
    if (latest) {
      await this.open(latest.conversationId);
      const opened = this.active();
      if (opened) return opened;
    }
    return this.startNew();
  }

  async startNew(): Promise<ConversationSummary> {
    this.stopPolling();
    this.error.set(null);
    const generation = ++this.generation;
    this.switching.set(true);
    try {
      const conversation = await this.api.start();
      if (generation !== this.generation) return conversation; // another switch happened meanwhile
      this.active.set(conversation);
      this.messages.set([]);
      this.pending.set(null);
      this.lastSequence = 0;
      this.conversations.update(list => [conversation, ...list.filter(c => c.conversationId !== conversation.conversationId)]);
      return conversation;
    } finally {
      if (generation === this.generation) this.switching.set(false);
    }
  }

  async open(conversationId: string): Promise<void> {
    this.stopPolling();
    this.loading.set(true);
    this.error.set(null);
    const generation = ++this.generation;
    this.switching.set(true);
    try {
      const detail = await this.api.get(conversationId);
      if (generation !== this.generation) return;
      this.active.set(detail.conversation);
      this.messages.set(detail.history.messages);
      this.lastSequence = detail.history.lastSequence;
      this.pending.set(null);
      if (detail.history.hasMore) await this.fetchNew();
    } catch (error) {
      this.error.set(hebrewError(error));
    } finally {
      if (generation === this.generation) {
        this.loading.set(false);
        this.switching.set(false);
      }
    }
  }

  async send(text: string): Promise<void> {
    const trimmed = text.trim();
    if (!trimmed || this.busy()) return;
    this.error.set(null);
    try {
      const conversation = await this.ensureActive();
      const submitted = await this.api.send(conversation.conversationId, trimmed);
      this.pending.set({inputId: submitted.inputId, text: trimmed, status: 'pending'});
      await this.fetchNew();
      this.schedulePoll();
    } catch (error) {
      this.error.set(hebrewError(error));
    }
  }

  async resend(): Promise<void> {
    const failed = this.failedTurn();
    if (!failed) return;
    this.pending.set(null);
    await this.send(failed.text);
  }

  /** While the advisor screen is open: pick up asynchronous continuations. */
  watch(): void {
    if (this.watchTimer) return;
    this.watchTimer = setInterval(() => {
      if (this.active() && !this.busy()) void this.fetchNew().catch(() => undefined);
    }, WATCH_MS);
  }

  unwatch(): void {
    if (this.watchTimer) clearInterval(this.watchTimer);
    this.watchTimer = null;
    this.stopPolling();
  }

  private schedulePoll(): void {
    this.stopPolling();
    this.pollTimer = setTimeout(() => void this.pollOnce(), POLL_MS);
  }

  private async pollOnce(): Promise<void> {
    const conversation = this.active();
    const turn = this.pending();
    if (!conversation || !turn) return;
    try {
      await this.fetchNew();
      const state = await this.api.input(conversation.conversationId, turn.inputId);
      this.pending.set({...turn, status: state.status});
      if (FINAL_INPUT_STATES.has(state.status)) {
        await this.fetchNew();
        return;
      }
    } catch (error) {
      this.error.set(hebrewError(error));
    }
    this.schedulePoll();
  }

  private async fetchNew(): Promise<void> {
    const conversation = this.active();
    if (!conversation) return;
    for (;;) {
      const page = await this.api.messages(conversation.conversationId, this.lastSequence);
      if (this.active()?.conversationId !== conversation.conversationId) return; // switched away
      if (page.messages.length) {
        const known = new Set(this.messages().map(m => m.messageId));
        this.messages.update(list => [...list, ...page.messages.filter(m => !known.has(m.messageId))]);
      }
      this.lastSequence = page.lastSequence;
      if (!page.hasMore) return;
    }
  }

  private stopPolling(): void {
    if (this.pollTimer) clearTimeout(this.pollTimer);
    this.pollTimer = null;
  }
}
