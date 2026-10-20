import {Component, OnInit, computed, inject, signal} from '@angular/core';
import {RouterLink} from '@angular/router';
import {FormsModule} from '@angular/forms';
import {FIELD_PATH, optionLabel} from '../../core/api/labels';
import type {OptionItem} from '../../core/api/types';
import {PageHeadingComponent} from '../../shared/ui/page-heading.component';
import {VehicleCardComponent} from '../../shared/ui/vehicle-card.component';
import {CatalogStore} from './state/catalog.store';

/**
 * Options and eligibility of the active conversation's case, exactly as the Business module
 * returned them (`eligibility`, `reasons`, amounts as decimal strings). No rule runs here.
 */
@Component({standalone: true, imports: [RouterLink, FormsModule, PageHeadingComponent, VehicleCardComponent], templateUrl: './catalog.page.html'})
export class CatalogPage implements OnInit {
  readonly store = inject(CatalogStore);
  readonly fieldPath = FIELD_PATH;
  readonly filter = signal<'all' | 'electric' | 'hybrid' | 'family' | 'waiver'>('all');
  readonly search = signal('');
  readonly filters = [
    {id: 'all', name: 'כל האפשרויות'},
    {id: 'electric', name: 'חשמלי'},
    {id: 'hybrid', name: 'היברידי'},
    {id: 'family', name: 'משפחתי'},
    {id: 'waiver', name: 'ויתור על רכב'},
  ] as const;

  readonly filtered = computed(() =>
    this.store.options().filter(o => this.matches(o) && this.label(o).toLowerCase().includes(this.search().toLowerCase())),
  );

  ngOnInit(): void {
    void this.store.load();
  }

  label(option: OptionItem): string {
    return optionLabel(option);
  }

  private matches(option: OptionItem): boolean {
    switch (this.filter()) {
      case 'all':
        return true;
      case 'waiver':
        return option.category === 'waiver';
      case 'family':
        return option.category === 'family';
      case 'electric':
        return option.propulsion === 'electric';
      case 'hybrid':
        return option.propulsion === 'hybrid' || option.propulsion === 'plug_in_hybrid';
    }
  }
}
