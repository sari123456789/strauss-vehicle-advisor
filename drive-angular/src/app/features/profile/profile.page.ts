import {Component, OnInit, computed, inject, signal} from '@angular/core';
import {RouterLink} from '@angular/router';
import {FIELD_STATUS, PROPULSION, SOURCE} from '../../core/api/labels';
import {hebrewError} from '../../core/api/problem';
import {SessionService} from '../../core/auth/session.service';
import {ConversationStore} from '../advisor/state/conversation.store';
import {PayslipImportComponent} from '../payslip/payslip-import.component';
import {MoneyPipe} from '../../shared/format/money.pipe';
import {PageHeadingComponent} from '../../shared/ui/page-heading.component';
import {ProfileStore} from './state/profile.store';

interface Row {
  /** Payslip field path for the six salary facts (`data-field`), otherwise null. */
  field: string | null;
  label: string;
  value: string | null;
  money: boolean;
  status: string;
  source: string | null;
}

const ARRANGEMENT: Record<string, string> = {
  company_car: 'רכב חברה',
  allowance: 'אחזקת רכב',
  no_car_no_allowance: 'ללא רכב וללא אחזקה',
};

/**
 * The saved EmployeeProfile, read-only: each field shows its value, status and source.
 * Corrections and "what if" values are given in the advisor conversation, which records them
 * as actual updates (with version checks) or as hypothetical values of that conversation.
 * The salary facts can also be imported from a payslip PDF: the employee reviews the draft and
 * explicitly approves it, then the profile is reloaded from the server.
 */
@Component({
  standalone: true,
  imports: [RouterLink, PageHeadingComponent, MoneyPipe, PayslipImportComponent],
  templateUrl: './profile.page.html',
})
export class ProfilePage implements OnInit {
  readonly store = inject(ProfileStore);
  readonly session = inject(SessionService);
  private readonly conversations = inject(ConversationStore);
  readonly fieldStatus = FIELD_STATUS;

  /** The conversation the import belongs to: the employee's active advisor conversation. */
  readonly importConversation = signal<string | null>(null);
  readonly importOpening = signal(false);
  readonly importError = signal<string | null>(null);

  readonly rows = computed<Row[]>(() => {
    const p = this.store.profile();
    if (!p) return [];
    const row = (
      label: string,
      field: {value?: unknown; status: string; source?: string | null},
      money = false,
      path: string | null = null,
    ): Row => ({
      field: path,
      label,
      value: field.value === null || field.value === undefined ? null : String(field.value),
      money,
      status: FIELD_STATUS[field.status] ?? field.status,
      source: field.source ? (SOURCE[field.source] ?? field.source) : null,
    });
    const arrangement = p.currentArrangement;
    const kind = arrangement.kind.value ? ARRANGEMENT[arrangement.kind.value] : null;
    const propulsion = arrangement.propulsion.value ? PROPULSION[arrangement.propulsion.value] : null;
    const vehicle = arrangement.vehicle.value?.catalogueId ?? null;
    return [
      row('שכר בסיס חודשי', p.baseGross, true, 'payroll.baseGross'),
      row('עמלות חודשיות', p.commissionsGross, true, 'payroll.commissionsGross'),
      row('בונוס חודשי', p.bonusGross, true, 'payroll.bonusGross'),
      row('הפרשת עובד לפנסיה', p.pensionEmployeeRate, false, 'payroll.pensionEmployeeRate'),
      row('הפרשת עובד לקרן השתלמות', p.studyFundEmployeeRate, false, 'payroll.studyFundEmployeeRate'),
      row('נקודות זיכוי', p.creditPoints, false, 'payroll.creditPoints'),
      row('עיסוק', p.occupationId),
      row('קבוצת זכאות', p.entitlementGroup),
      row('ילדים מזכים', p.qualifyingChildrenCount),
      {...row('הסדר רכב נוכחי', arrangement.kind), value: kind},
      {...row('רכב נוכחי', arrangement.vehicle), value: vehicle},
      {...row('סוג הנעה', arrangement.propulsion), value: propulsion},
      row('השתתפות נוכחית ברכב', arrangement.participation, true),
      row('אחזקת רכב נוכחית', arrangement.allowance, true),
    ];
  });

  ngOnInit(): void {
    void this.store.load();
  }

  /** Opens the payslip import inside the employee's active conversation (reopened or started). */
  async openImport(): Promise<void> {
    if (this.importOpening()) return;
    this.importOpening.set(true);
    this.importError.set(null);
    try {
      const conversation = await this.conversations.ensureActive();
      this.importConversation.set(conversation.conversationId);
    } catch (error) {
      this.importError.set(hebrewError(error));
    } finally {
      this.importOpening.set(false);
    }
  }

  closeImport(): void {
    this.importConversation.set(null);
  }
}
