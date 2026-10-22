import {Component, OnInit, computed, inject} from '@angular/core';
import {RouterLink} from '@angular/router';
import {ComparisonsStore, HistoryRow} from './state/comparisons.store';

/** Saved conversations and calculations, recovered from the server (never browser storage). */
@Component({standalone: true, imports: [RouterLink], templateUrl: './history.page.html', styleUrl: './history.page.scss'})
export class HistoryPage implements OnInit {
  readonly store = inject(ComparisonsStore);

  ngOnInit(): void {
    void this.store.loadHistory();
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
