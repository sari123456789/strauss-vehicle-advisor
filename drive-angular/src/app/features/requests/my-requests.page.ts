import {Component, OnInit, inject, signal} from '@angular/core';
import {DatePipe} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {RouterLink} from '@angular/router';
import {BusinessApi} from '../../core/api/business.api';
import {FIELD_CODE} from '../../core/api/labels';
import {hebrewError} from '../../core/api/problem';
import type {MyRequestView} from '../../core/api/types';
import {ConversationStore} from '../advisor/state/conversation.store';

/** Closed topic list: the form only accepts company-car questions (no free subject). */
const TOPICS = [
  'החלפת רכב',
  'מסירה וקבלת רכב',
  'ביטוח ותאונות',
  'תחזוקה וטיפולים',
  'עזיבה או מעבר תפקיד',
  'זכאות ונהלים',
  'אחר בנושא רכב החברה',
];

const STATUS: Record<MyRequestView['status'], string> = {
  open: 'ממתינה לתשובה',
  answered: 'נענתה',
  cannot_determine: 'לא ניתן היה לענות',
  cancelled: 'בוטלה',
};

/**
 * The employee's requests to a transportation representative: a stand-alone form (general
 * company-car question, closed topic list) and every request with its status and answer.
 * Answers also arrive in the advisor chat of the active conversation.
 */
@Component({standalone: true, imports: [FormsModule, DatePipe, RouterLink], templateUrl: './my-requests.page.html', styleUrl: './my-requests.page.scss'})
export class MyRequestsPage implements OnInit {
  private readonly api = inject(BusinessApi);
  private readonly conversations = inject(ConversationStore);
  readonly topics = TOPICS;
  readonly status = STATUS;
  readonly fieldCode = FIELD_CODE;
  readonly requests = signal<MyRequestView[]>([]);
  readonly loading = signal(false);
  readonly sending = signal(false);
  readonly error = signal<string | null>(null);
  readonly sent = signal(false);
  topic = '';
  text = '';

  ngOnInit(): void {
    void this.load();
  }

  async load(): Promise<void> {
    this.loading.set(true);
    try {
      const envelope = await this.api.myRequests();
      this.requests.set(envelope.data ?? []);
    } catch (error) {
      this.error.set(hebrewError(error));
    } finally {
      this.loading.set(false);
    }
  }

  async send(): Promise<void> {
    const text = this.text.trim();
    if (!this.topic || text.length < 5) return;
    this.sending.set(true);
    this.error.set(null);
    this.sent.set(false);
    try {
      const conversation = await this.conversations.ensureActive();
      if (!conversation.caseId) throw new Error('conversation without case');
      const context = (await this.api.caseContext(conversation.caseId)).data;
      if (!context) throw new Error('case context unavailable');
      await this.api.askRepresentative(context, 'general.question', null, `${this.topic}: ${text}`.slice(0, 1000), 'form');
      this.text = '';
      this.topic = '';
      this.sent.set(true);
      await this.load();
    } catch (error) {
      this.error.set(hebrewError(error));
    } finally {
      this.sending.set(false);
    }
  }

  answerText(request: MyRequestView): string | null {
    const answer = request.answer;
    if (!answer) return null;
    return answer.text ?? answer.value ?? null;
  }
}
