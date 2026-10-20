import {Component, OnInit, computed, inject} from '@angular/core';
import {RouterLink} from '@angular/router';
import {FIELD_STATUS, PROPULSION, SOURCE} from '../../core/api/labels';
import {MoneyPipe} from '../../shared/format/money.pipe';
import {PageHeadingComponent} from '../../shared/ui/page-heading.component';
import {ProfileStore} from './state/profile.store';

interface Row {
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
 */
@Component({standalone: true, imports: [RouterLink, PageHeadingComponent, MoneyPipe], templateUrl: './profile.page.html'})
export class ProfilePage implements OnInit {
  readonly store = inject(ProfileStore);
  readonly fieldStatus = FIELD_STATUS;

  readonly rows = computed<Row[]>(() => {
    const p = this.store.profile();
    if (!p) return [];
    const row = (label: string, field: {value?: unknown; status: string; source?: string | null}, money = false): Row => ({
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
      row('שכר בסיס חודשי', p.baseGross, true),
      row('עמלות חודשיות', p.commissionsGross, true),
      row('בונוס חודשי', p.bonusGross, true),
      row('הפרשת עובד לפנסיה', p.pensionEmployeeRate),
      row('הפרשת עובד לקרן השתלמות', p.studyFundEmployeeRate),
      row('נקודות זיכוי', p.creditPoints),
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
}
