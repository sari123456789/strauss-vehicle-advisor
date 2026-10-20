import {Component, OnInit, computed, inject, signal} from '@angular/core';
import {ActivatedRoute, RouterLink} from '@angular/router';
import {SCENARIO_STATUS} from '../../core/api/labels';
import type {CalculationBreakdown, ScenarioResult} from '../../core/api/types';
import {MoneyPipe} from '../../shared/format/money.pipe';
import {PageHeadingComponent} from '../../shared/ui/page-heading.component';
import {CatalogStore} from '../catalog/state/catalog.store';
import {ComparisonsStore} from './state/comparisons.store';
import {optionLabel} from '../../core/api/labels';

const ROWS: {key: keyof CalculationBreakdown; label: string}[] = [
  {key: 'earnedGross', label: 'שכר ברוטו חודשי'},
  {key: 'valueOfUse', label: 'שווי שימוש ברכב'},
  {key: 'allowance', label: 'אחזקת רכב'},
  {key: 'electricGrossUp', label: 'החזר חשמל (מגולם)'},
  {key: 'taxableBase', label: 'הכנסה חייבת'},
  {key: 'incomeTax', label: 'מס הכנסה'},
  {key: 'nationalInsurance', label: 'ביטוח לאומי'},
  {key: 'healthInsurance', label: 'ביטוח בריאות'},
  {key: 'pensionEmployee', label: 'הפרשת עובד לפנסיה'},
  {key: 'studyFundEmployee', label: 'הפרשת עובד לקרן השתלמות'},
  {key: 'ordinaryParticipation', label: 'השתתפות ברכב'},
  {key: 'familyGapContribution', label: 'השתתפות בפער מסלול משפחה'},
];

/**
 * The stored, immutable calculation of one run (`GET /cases/{id}/comparisons/{runId}`), from
 * the comparison page, a conversation message or the history. Component amounts are the
 * Business breakdown; nothing is recalculated in the browser.
 */
@Component({standalone: true, imports: [RouterLink, PageHeadingComponent, MoneyPipe], templateUrl: './calculation.page.html'})
export class CalculationPage implements OnInit {
  readonly store = inject(ComparisonsStore);
  private readonly catalog = inject(CatalogStore);
  private readonly route = inject(ActivatedRoute);
  readonly rows = ROWS;
  readonly statuses = SCENARIO_STATUS;
  readonly chosen = signal<string | null>(null);
  readonly result = computed(() => this.store.details()?.result ?? null);
  readonly alternative = computed<ScenarioResult | null>(() => {
    const alternatives = this.result()?.alternatives ?? [];
    return alternatives.find(a => a.optionKey === this.chosen()) ?? alternatives[0] ?? null;
  });

  ngOnInit(): void {
    const query = this.route.snapshot.queryParamMap;
    const caseId = query.get('case');
    const runId = query.get('run');
    if (caseId && runId) void this.store.loadDetails(caseId, runId);
  }

  label(alternative: ScenarioResult): string {
    const option = this.catalog.options().find(o => o.optionKey === alternative.optionKey);
    return option ? optionLabel(option) : alternative.optionKey === 'waiver' ? 'ויתור על רכב' : alternative.optionKey;
  }

  value(breakdown: CalculationBreakdown | null | undefined, key: keyof CalculationBreakdown): string | null {
    const amount = breakdown?.[key];
    return amount && typeof amount === 'object' ? amount.display : null;
  }
}
