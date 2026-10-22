import {FIELD_STATUS, PROPULSION, SOURCE} from '../../core/api/labels';
import type {EmployeeProfileView} from '../../core/api/types';

export interface ProfileRow {
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

/** Every EmployeeProfile field the calculation needs, in display order, missing ones included. */
export function profileRows(p: EmployeeProfileView | null): ProfileRow[] {
  if (!p) return [];
  const row = (
    label: string,
    field: {value?: unknown; status: string; source?: string | null},
    money = false,
    path: string | null = null,
  ): ProfileRow => ({
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
}
