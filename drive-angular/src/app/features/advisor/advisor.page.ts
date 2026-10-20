import {Component, OnDestroy, OnInit, inject, signal} from '@angular/core';
import {ActivatedRoute, Router, RouterLink} from '@angular/router';
import {FormsModule} from '@angular/forms';
import {FIELD_STATUS} from '../../core/api/labels';
import type {OptionItem} from '../../core/api/types';
import {CarArtComponent} from '../../shared/ui/car-art.component';
import {MoneyPipe} from '../../shared/format/money.pipe';
import {PageHeadingComponent} from '../../shared/ui/page-heading.component';
import {VehicleCardComponent} from '../../shared/ui/vehicle-card.component';
import {CatalogStore} from '../catalog/state/catalog.store';
import {PayslipImportComponent} from '../payslip/payslip-import.component';
import {ProfileStore} from '../profile/state/profile.store';
import {ConversationStore} from './state/conversation.store';

/** Message kinds that carry a Business comparison run the employee can open in detail. */
const RUN_KINDS = new Set(['comparison', 'history']);

@Component({
  standalone: true,
  imports: [RouterLink, FormsModule, CarArtComponent, PageHeadingComponent, MoneyPipe, PayslipImportComponent, VehicleCardComponent],
  templateUrl: './advisor.page.html',
})
export class AdvisorPage implements OnInit, OnDestroy {
  readonly store = inject(ConversationStore);
  readonly profile = inject(ProfileStore);
  /** Catalogue options of the case: the data behind the vehicle cards in `options` messages. */
  readonly catalog = inject(CatalogStore);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  readonly fieldStatus = FIELD_STATUS;
  /** The payslip import panel (same reusable component as the profile page). */
  readonly payslipOpen = signal(false);
  text = '';

  async ngOnInit(): Promise<void> {
    void this.profile.load();
    const requested = this.route.snapshot.paramMap.get('conversationId');
    if (requested) await this.store.open(requested);
    else await this.store.ensureActive().catch(() => undefined);
    void this.store.loadConversations();
    void this.catalog.load();
    this.store.watch();
  }

  ngOnDestroy(): void {
    this.store.unwatch();
  }

  send(): void {
    const text = this.text;
    this.text = '';
    void this.store.send(text).then(() => {
      void this.profile.load();
      void this.catalog.load();
    });
  }

  async startNew(): Promise<void> {
    await this.store.startNew();
    await this.router.navigate(['/advisor', this.store.active()?.conversationId]);
  }

  async switchTo(conversationId: string): Promise<void> {
    await this.router.navigate(['/advisor', conversationId]);
    await this.store.open(conversationId);
  }

  /**
   * Cards for an `options` message: the option keys it lists, in its order, with the Business
   * data of the case's catalogue. Keys the catalogue does not return are left out (the text
   * above them still lists every option).
   */
  cardsOf(message: {kind: string; payload: Record<string, unknown>}): OptionItem[] {
    const keys = message.payload['optionKeys'];
    if (message.kind !== 'options' || !Array.isArray(keys)) return [];
    const byKey = new Map(this.catalog.options().map(o => [o.optionKey, o]));
    return keys.flatMap(key => {
      const option = typeof key === 'string' ? byKey.get(key) : undefined;
      return option ? [option] : [];
    });
  }

  runOf(message: {kind: string; payload: Record<string, unknown>}): string | null {
    const runId = message.payload['runId'];
    return RUN_KINDS.has(message.kind) && typeof runId === 'string' ? runId : null;
  }
}
