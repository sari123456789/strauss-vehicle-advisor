import {Component, OnDestroy, OnInit, inject} from '@angular/core';
import {DatePipe} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {FIELD_CODE, REQUEST_STATUS} from '../../core/api/labels';
import type {HandlingStatus, RepresentativeRequestView} from '../../core/api/types';
import {AnswerDraft, InquiriesStore, QueueTab} from './state/inquiries.store';

const GROUPS = ['א', 'ב', 'ג', 'ד'];
const PROPULSIONS = [
  {value: 'petrol', label: 'בנזין'},
  {value: 'hybrid', label: 'היברידי'},
  {value: 'plug_in_hybrid', label: 'פלאג-אין'},
  {value: 'electric', label: 'חשמלי'},
];

const HANDLING: Record<HandlingStatus, string> = {
  unclaimed: 'ממתינה לטיפול',
  assigned_to_me: 'בטיפולי',
  assigned_to_other: 'בטיפול נציג אחר',
  completed: 'נענתה',
};

/**
 * Representative request centre: the shared queue with counts, tabs, search and topic filter,
 * and a detail panel to claim, answer or release one request. Only minimal request context is
 * shown (never the employee's identity or salary); every rule is enforced by the server.
 */
@Component({standalone: true, imports: [FormsModule, DatePipe], templateUrl: './representative.page.html', styleUrl: './representative.page.scss'})
export class RepresentativePage implements OnInit, OnDestroy {
  readonly store = inject(InquiriesStore);
  readonly fieldCode = FIELD_CODE;
  readonly fieldCodes = Object.entries(FIELD_CODE);
  readonly requestStatus = REQUEST_STATUS;
  readonly groups = GROUPS;
  readonly propulsions = PROPULSIONS;
  readonly tabs: {id: QueueTab; label: string; count: () => number | null}[] = [
    {id: 'all', label: 'כל הפניות', count: () => this.total()},
    {id: 'unclaimed', label: 'ממתינות', count: () => this.store.summary()?.unclaimed ?? null},
    {id: 'mine', label: 'בטיפולי', count: () => this.store.summary()?.assignedToMe ?? null},
    {id: 'others', label: 'בטיפול אחרים', count: () => this.store.summary()?.assignedToOthers ?? null},
    {id: 'answered', label: 'נענו', count: () => this.answered()},
  ];
  /** The server's money format (whole shekels or agorot); the server validates it again. */
  readonly moneyPattern = '^\\d{1,9}(\\.\\d{1,2})?$';
  draft: AnswerDraft = this.empty();
  searchText = '';
  sent = false;

  ngOnInit(): void {
    void this.store.load();
    this.store.watch();
  }

  ngOnDestroy(): void {
    this.store.unwatch();
  }

  total(): number | null {
    const s = this.store.summary();
    return s ? s.unclaimed + s.assignedToMe + s.assignedToOthers + s.answered + s.cannotDetermine : null;
  }

  answered(): number | null {
    const s = this.store.summary();
    return s ? s.answered + s.cannotDetermine : null;
  }

  /** Short reference for the table (the full ID stays in the detail panel). */
  ref(request: RepresentativeRequestView): string {
    return request.requestId.replace(/-/g, '').slice(0, 6).toUpperCase();
  }

  handling(request: RepresentativeRequestView): HandlingStatus {
    if (request.status !== 'open') return 'completed';
    return request.handlingStatus ?? 'unclaimed';
  }

  handlingLabel(request: RepresentativeRequestView): string {
    if (request.status !== 'open') return this.requestStatus[request.status];
    return HANDLING[this.handling(request)];
  }

  handler(request: RepresentativeRequestView): string {
    if (this.handling(request) === 'assigned_to_me') return 'אני';
    return request.handlerDisplayName ?? '—';
  }

  open(request: RepresentativeRequestView): void {
    this.store.select(request.requestId);
    this.draft = this.empty();
    this.sent = false;
  }

  async claim(request: RepresentativeRequestView): Promise<void> {
    if (await this.store.claim(request)) this.open(request);
  }

  async release(request: RepresentativeRequestView): Promise<void> {
    await this.store.release(request);
  }

  search(): void {
    this.store.setSearch(this.searchText);
  }

  /** Values the server cannot apply yet (decisions Q2/Q3) can only be "cannot determine". */
  valueAllowed(request: RepresentativeRequestView): boolean {
    return request.expectedValueType === 'money' || request.expectedValueType === 'entitlement_group' || request.expectedValueType === 'propulsion';
  }

  async submit(request: RepresentativeRequestView): Promise<void> {
    if (!this.valueAllowed(request)) this.draft.outcome = 'cannot_determine';
    this.sent = await this.store.answer(request, this.draft);
    if (this.sent) this.draft = this.empty();
  }

  private empty(): AnswerDraft {
    return {outcome: 'answered', value: '', effectiveFrom: '', effectiveTo: '', note: ''};
  }
}
