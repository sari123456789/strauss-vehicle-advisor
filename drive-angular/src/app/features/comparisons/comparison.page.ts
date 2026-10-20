import {Component, OnInit, computed, inject} from '@angular/core';
import {RouterLink} from '@angular/router';
import {ELIGIBILITY_REASON, FIELD_PATH, SCENARIO_STATUS, optionLabel} from '../../core/api/labels';
import type {ScenarioResult} from '../../core/api/types';
import {MoneyPipe} from '../../shared/format/money.pipe';
import {CarArtComponent} from '../../shared/ui/car-art.component';
import {PageHeadingComponent} from '../../shared/ui/page-heading.component';
import {CatalogStore} from '../catalog/state/catalog.store';
import {ComparisonsStore} from './state/comparisons.store';

/**
 * A real comparison of the selected options against the current arrangement
 * (`POST /cases/{id}/comparisons`). Every amount is the Business `display` value; a scenario
 * that is not calculated shows its status and the missing facts instead of a number.
 */
@Component({standalone: true, imports: [RouterLink, CarArtComponent, PageHeadingComponent, MoneyPipe], templateUrl: './comparison.page.html'})
export class ComparisonPage implements OnInit {
  readonly store = inject(ComparisonsStore);
  readonly catalog = inject(CatalogStore);
  readonly statuses = SCENARIO_STATUS;
  readonly reasons = ELIGIBILITY_REASON;
  readonly fieldPath = FIELD_PATH;
  readonly result = this.store.result;
  readonly labels = computed(() => new Map(this.catalog.options().map(o => [o.optionKey, optionLabel(o)])));

  async ngOnInit(): Promise<void> {
    if (!this.catalog.options().length) await this.catalog.load();
    await this.run();
  }

  run(): Promise<void> {
    return this.store.compare(this.catalog.selected());
  }

  label(alternative: ScenarioResult): string {
    return this.labels().get(alternative.optionKey) ?? alternative.optionKey;
  }

  ask(alternative: ScenarioResult): void {
    void this.store.askParticipation(alternative.optionKey, this.label(alternative));
  }
}
