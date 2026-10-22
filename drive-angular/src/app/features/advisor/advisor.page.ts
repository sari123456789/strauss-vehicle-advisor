import {Component, OnDestroy, OnInit, computed, inject, signal} from '@angular/core';
import {ActivatedRoute, Router, RouterLink} from '@angular/router';
import {KeyValuePipe} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {BusinessApi} from '../../core/api/business.api';
import {FIELD_STATUS} from '../../core/api/labels';
import type {OptionItem} from '../../core/api/types';
import {CarArtComponent} from '../../shared/ui/car-art.component';
import {MoneyPipe} from '../../shared/format/money.pipe';
import {PageHeadingComponent} from '../../shared/ui/page-heading.component';
import {VehicleCardComponent} from '../../shared/ui/vehicle-card.component';
import {CatalogStore} from '../catalog/state/catalog.store';
import {PayslipImportComponent} from '../payslip/payslip-import.component';
import {ProfileStore} from '../profile/state/profile.store';
import {profileRows} from '../profile/profile-rows';
import {ConversationStore} from './state/conversation.store';
import {compareMessage} from './compare-message';
import {vehicleImageUrl} from '../../shared/ui/vehicle-images';

/** Message kinds that carry a Business comparison run the employee can open in detail. */
const RUN_KINDS = new Set(['comparison', 'history']);

@Component({
  standalone: true,
  imports: [RouterLink, FormsModule, KeyValuePipe, CarArtComponent, PageHeadingComponent, MoneyPipe, PayslipImportComponent, VehicleCardComponent],
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
  /** All profile fields for the side panel; missing ones are shown as not yet known. */
  readonly profileRows = computed(() => profileRows(this.profile.profile()));
  /** The payslip import panel (reusable component). */
  readonly payslipOpen = signal(false);
  readonly currentCarImage = computed(() =>
    vehicleImageUrl(this.profile.profile()?.currentArrangement.vehicle.value?.catalogueId),
  );
  /** Vehicles shown in the side panel, opened from an `options` message; null when closed. */
  readonly panelCards = signal<OptionItem[] | null>(null);
  text = '';
  /** The profile field being edited in the side panel; the new value goes to the advisor. */
  readonly editing = signal<string | null>(null);
  editValue = '';

  async ngOnInit(): Promise<void> {
    void this.profile.load();
    const requested = this.route.snapshot.paramMap.get('conversationId');
    if (requested) await this.store.open(requested);
    // Within the app (back from the catalogue or the comparison) the current conversation goes on;
    // arriving at the chat afresh starts a new one.
    else if (!this.store.active()) await this.store.startFresh().catch(() => undefined);
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

  startEdit(label: string): void {
    this.editing.set(label);
    this.editValue = '';
  }

  /** Profile updates keep the advisor's checks and provenance: the edit is sent as a message. */
  submitEdit(label: string): void {
    const value = this.editValue.trim();
    if (!value) return;
    this.editing.set(null);
    this.text = `עדכון לפרטים שלי — ${label}: ${value}`;
    this.send();
  }

  async startNew(): Promise<void> {
    await this.store.startNew();
    await this.router.navigate(['/advisor', this.store.active()?.conversationId]);
  }

  async deleteConversation(conversationId: string): Promise<void> {
    if (!confirm('למחוק את השיחה מהרשימה?')) return;
    const wasActive = this.store.active()?.conversationId === conversationId;
    await this.store.remove(conversationId);
    if (wasActive && !this.store.active()) {
      const next = await this.store.startFresh().catch(() => undefined);
      if (next) await this.router.navigate(['/advisor', next.conversationId]);
    }
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
    if ((message.kind !== 'options' && message.kind !== 'comparison') || !Array.isArray(keys)) return [];
    const byKey = new Map(this.catalog.options().map(o => [o.optionKey, o]));
    return keys.flatMap(key => {
      const option = typeof key === 'string' ? byKey.get(key) : undefined;
      return option ? [option] : [];
    });
  }

  /** Differences shown on the cards of the open side panel (a comparison), or none. */
  readonly panelDeltas = signal<Record<string, string>>({});
  private readonly businessApi = inject(BusinessApi);

  /**
   * Opens the side panel. For a comparison the monthly differences are read from the stored
   * run (amounts never travel in chat message payloads).
   */
  async openVehicles(cards: OptionItem[], message?: {kind: string; payload: Record<string, unknown>}): Promise<void> {
    this.panelDeltas.set({});
    this.panelCards.set(cards);
    const runId = message?.kind === 'comparison' ? message.payload['runId'] : null;
    const caseId = this.store.active()?.caseId;
    if (typeof runId !== 'string' || !caseId) return;
    try {
      const details = (await this.businessApi.comparisonDetails(caseId, runId)).data;
      const deltas: Record<string, string> = {};
      for (const a of details?.result.alternatives ?? []) {
        if (!a.delta) continue;
        const value = Number(a.delta.display);
        const text = `${Math.abs(value).toLocaleString('he-IL')} ₪`;
        deltas[a.optionKey] = value > 0 ? `+${text}` : value < 0 ? `-${text}` : `0 ₪`;
      }
      this.panelDeltas.set(deltas);
    } catch {
      // The cards still show without the differences.
    }
  }

  /**
   * The selected vehicles go to the advisor as a message, so the chat asks only what it still
   * needs (e.g. one confirmation of saved details) and shows the difference from today itself.
   */
  compareInChat(): void {
    const message = compareMessage(this.catalog.selectedOptions());
    if (!message || this.store.busy()) return;
    this.text = message;
    this.panelCards.set(null);
    this.catalog.clear();
    this.send();
  }

  runOf(message: {kind: string; payload: Record<string, unknown>}): string | null {
    const runId = message.payload['runId'];
    return RUN_KINDS.has(message.kind) && typeof runId === 'string' ? runId : null;
  }
}
