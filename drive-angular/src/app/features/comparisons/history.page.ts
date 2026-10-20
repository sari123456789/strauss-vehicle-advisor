import {Component, OnInit, inject} from '@angular/core';
import {RouterLink} from '@angular/router';
import {PageHeadingComponent} from '../../shared/ui/page-heading.component';
import {ComparisonsStore} from './state/comparisons.store';

/** Saved conversations and calculations, recovered from the server (never browser storage). */
@Component({standalone: true, imports: [RouterLink, PageHeadingComponent], templateUrl: './history.page.html'})
export class HistoryPage implements OnInit {
  readonly store = inject(ComparisonsStore);

  ngOnInit(): void {
    void this.store.loadHistory();
  }

  date(iso: string): string {
    return new Date(iso).toLocaleString('he-IL', {dateStyle: 'short', timeStyle: 'short'});
  }
}
