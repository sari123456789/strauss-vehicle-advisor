/** Hebrew display labels for contract enum values (presentation only, no business logic). */
import type {EligibilityReason, OptionItem, Propulsion, RepresentativeFieldCode} from './types';

export const ELIGIBILITY_REASON: Record<EligibilityReason, string> = {
  group_own: 'הרכב בקבוצת הזכאות שלך',
  group_lower: 'הרכב מקבוצה נמוכה מקבוצת הזכאות שלך',
  group_higher_than_entitlement: 'הרכב מקבוצה גבוהה מקבוצת הזכאות שלך',
  group_unknown: 'קבוצת הזכאות עדיין לא ידועה',
  family_children_met: 'עומד בתנאי מסלול המשפחה',
  family_children_not_met: 'לא עומד בתנאי מסלול המשפחה',
  family_children_unknown: 'מספר הילדים עדיין לא ידוע',
  waiver_allowed_by_occupation: 'העיסוק מאפשר ויתור על רכב',
  waiver_not_allowed_mandatory_car: 'בעיסוק הזה רכב חברה הוא חובה',
  occupation_unknown: 'העיסוק עדיין לא ידוע',
  occupation_not_in_table: 'העיסוק אינו מופיע בטבלת העיסוקים',
  occupation_group_conflict: 'העיסוק וקבוצת הזכאות אינם תואמים',
};

export const PROPULSION: Record<Propulsion, string> = {
  petrol: 'בנזין',
  hybrid: 'היברידי',
  plug_in_hybrid: 'פלאג-אין',
  electric: 'חשמלי',
};

export const ELIGIBILITY: Record<string, string> = {
  eligible: 'זכאי',
  ineligible: 'לא זכאי',
  needs_information: 'חסר מידע לבדיקה',
};

export const SCENARIO_STATUS: Record<string, string> = {
  calculated: 'חושב',
  ineligible: 'לא זמין לפי הכללים',
  needs_information: 'חסר מידע לחישוב',
  unsupported: 'לא נתמך במערכת',
  failed: 'החישוב לא הושלם',
};

export const FIELD_CODE: Record<RepresentativeFieldCode, string> = {
  'alternative.participation': 'דמי השתתפות ברכב',
  'occupation.waiverPolicy': 'אפשרות ויתור על רכב לפי העיסוק',
  'employee.entitlementGroup': 'קבוצת זכאות',
  'currentArrangement.propulsion': 'סוג ההנעה של הרכב הנוכחי',
  'currentArrangement.vehicle': 'פרטי הרכב הנוכחי',
};

export const REQUEST_STATUS: Record<string, string> = {
  open: 'ממתינה למענה',
  answered: 'נענתה',
  cannot_determine: 'לא ניתן לקבוע',
  cancelled: 'בוטלה',
};

export const FIELD_STATUS: Record<string, string> = {
  usable: 'קיים',
  missing: 'חסר',
  needs_clarification: 'דורש הבהרה',
};

export const SOURCE: Record<string, string> = {
  employee: 'דיווח העובד',
  catalogue: 'קטלוג',
  rules: 'כללים',
  tax_dataset: 'מאגר רשות המסים',
  representative: 'נציג התחבורה',
};

export const FIELD_PATH: Record<string, string> = {
  'payroll.baseGross': 'שכר בסיס',
  'payroll.commissionsGross': 'עמלות',
  'payroll.bonusGross': 'בונוס',
  'payroll.pensionEmployeeRate': 'שיעור הפרשת עובד לפנסיה',
  'payroll.studyFundEmployeeRate': 'שיעור הפרשת עובד לקרן השתלמות',
  'payroll.creditPoints': 'נקודות זיכוי',
  'employee.occupationId': 'עיסוק',
  'employee.entitlementGroup': 'קבוצת זכאות',
  'employee.qualifyingChildrenCount': 'מספר ילדים מזכים',
  'currentArrangement.kind': 'סוג ההסדר הנוכחי',
  'currentArrangement.vehicle': 'הרכב הנוכחי',
  'currentArrangement.propulsion': 'סוג ההנעה של הרכב הנוכחי',
  'currentArrangement.participation': 'השתתפות נוכחית ברכב',
  'currentArrangement.familyDeduction': 'ניכוי מסלול משפחה',
  'currentArrangement.familyDeductionIncludedInParticipation': 'האם ניכוי המשפחה כלול בהשתתפות',
  'currentArrangement.allowance': 'אחזקת רכב נוכחית',
};

export function optionLabel(option: Pick<OptionItem, 'optionKey' | 'commercialName' | 'manufacturerName' | 'catalogueId'>): string {
  if (option.optionKey === 'waiver') return 'ויתור על רכב — אחזקת רכב';
  const name = [option.manufacturerName, option.commercialName].filter(Boolean).join(' ');
  return name || option.catalogueId || option.optionKey;
}
