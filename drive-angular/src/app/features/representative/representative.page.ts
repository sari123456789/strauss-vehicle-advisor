import {Component, OnInit, inject} from '@angular/core';
import {FormsModule} from '@angular/forms';
import {FIELD_CODE, REQUEST_STATUS} from '../../core/api/labels';
import type {RepresentativeRequestView} from '../../core/api/types';
import {PageHeadingComponent} from '../../shared/ui/page-heading.component';
import {AnswerDraft, InquiriesStore} from './state/inquiries.store';

const GROUPS = ['א', 'ב', 'ג', 'ד'];
const PROPULSIONS = [
  {value: 'petrol', label: 'בנזין'},
  {value: 'hybrid', label: 'היברידי'},
  {value: 'plug_in_hybrid', label: 'פלאג-אין'},
  {value: 'electric', label: 'חשמלי'},
];

/**
 * Representative inbox: minimal request context only (never the employee's identity or salary)
 * and a structured answer form per `expectedValueType`. The employee's conversation continues
 * automatically once the answer is saved.
 */
@Component({standalone: true, imports: [FormsModule, PageHeadingComponent], templateUrl: './representative.page.html'})
export class RepresentativePage implements OnInit {
  readonly store = inject(InquiriesStore);
  readonly fieldCode = FIELD_CODE;
  readonly requestStatus = REQUEST_STATUS;
  readonly groups = GROUPS;
  readonly propulsions = PROPULSIONS;
  /** The server's money format (whole shekels or agorot); the server validates it again. */
  readonly moneyPattern = '^\\d{1,9}(\\.\\d{1,2})?$';
  draft: AnswerDraft = this.empty();
  sent = false;

  ngOnInit(): void {
    void this.store.load();
  }

  select(request: RepresentativeRequestView): void {
    this.store.select(request.requestId);
    this.draft = this.empty();
    this.sent = false;
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
