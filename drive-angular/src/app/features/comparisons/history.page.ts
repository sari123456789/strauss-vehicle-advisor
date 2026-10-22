import {Component, OnInit, computed, inject} from '@angular/core';
import {RouterLink} from '@angular/router';
import {optionLabel} from '../../core/api/labels';
import type {RunSummary} from '../../core/api/types';
import {CatalogStore} from '../catalog/state/catalog.store';
import {ComparisonsStore, HistoryRow} from './state/comparisons.store';

/** Saved conversations and calculations, recovered from the server (never browser storage). */
@Component({standalone: true, imports: [RouterLink], templateUrl: './history.page.html', styleUrl: './history.page.scss'})
export class HistoryPage implements OnInit {
  readonly store = inject(ComparisonsStore);

  private readonly catalog = inject(CatalogStore);
  private readonly labels = computed(() => new Map(this.catalog.options().map(o => [o.optionKey, optionLabel(o)])));

  ngOnInit(): void {
    void this.store.loadHistory();
    if (!this.catalog.options().length) void this.catalog.load();
  }

  /** "3 רכבים · הכי משתלם: MG4" from the stored result summary; null for older runs. */
  summary(run: RunSummary): string | null {
    const count = run.optionCount;
    if (!count) return null;
    const cars = count === 1 ? 'רכב אחד' : `${count} רכבים`;
    if (!run.bestOptionKey) return cars;
    const label = this.labels().get(run.bestOptionKey) ?? run.bestOptionKey.replace('vehicle:', '');
    if (count === 1) return label;
    return run.bestDelta == null ? cars : `${cars} · הכי משתלם: ${label}`;
  }

  delta(run: RunSummary): string | null {
    if (run.bestDelta == null) return null;
    const value = Number(run.bestDelta);
    const text = `${Math.abs(value).toLocaleString('he-IL')} ₪`;
    return value > 0 ? `+${text}` : value < 0 ? `-${text}` : '0 ₪';
  }

  /** Runs grouped by day ("היום", "אתמול", or the date), newest first. */
  readonly groups = computed(() => {
    const today = new Date().toDateString();
    const yesterday = new Date(Date.now() - 86_400_000).toDateString();
    const groups: {label: string; rows: HistoryRow[]}[] = [];
    for (const row of this.store.history()) {
      const day = new Date(row.run.createdAt);
      const key = day.toDateString();
      const label = key === today ? 'היום' : key === yesterday ? 'אתמול' : day.toLocaleDateString('he-IL', {day: 'numeric', month: 'long', year: 'numeric'});
      const last = groups.at(-1);
      if (last?.label === label) last.rows.push(row);
      else groups.push({label, rows: [row]});
    }
    return groups;
  });

  time(iso: string): string {
    return new Date(iso).toLocaleTimeString('he-IL', {hour: '2-digit', minute: '2-digit'});
  }
}
