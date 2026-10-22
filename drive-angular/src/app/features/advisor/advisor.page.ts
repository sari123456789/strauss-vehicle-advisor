import {Component, OnDestroy, OnInit, computed, inject, signal} from '@angular/core';
import {ActivatedRoute, Router, RouterLink} from '@angular/router';
import {FormsModule} from '@angular/forms';
import {FIELD_STATUS, PROPULSION, optionLabel} from '../../core/api/labels';

const ARRANGEMENT: Record<string, string> = {
  company_car: 'רכב חברה',
  allowance: 'אחזקת רכב',
  no_car_no_allowance: 'ללא רכב וללא אחזקה',
};
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
import {ComparisonsStore} from '../comparisons/state/comparisons.store';
import {vehicleImageUrl} from '../../shared/ui/vehicle-images';

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
  /** All profile fields for the side panel; missing ones are shown as not yet known. */
  readonly profileRows = computed(() => profileRows(this.profile.profile()));
  /** The payslip import panel (reusable component). */
  readonly payslipOpen = signal(false);
  /** Sent here from the comparison page because the current state could not be calculated. */
  readonly needsCurrent = inject(ComparisonsStore).needsCurrent;
  readonly currentCarImage = computed(() =>
    vehicleImageUrl(this.profile.profile()?.currentArrangement.vehicle.value?.catalogueId),
  );
  /** The saved current-car details the employee is asked to confirm, in plain words. */
  readonly currentDetails = computed(() => {
    const a = this.profile.profile()?.currentArrangement;
    if (!a) return [];
    const missing = 'לא ידוע';
    const participation = a.participation.value;
    return [
      {label: 'סוג ההסדר', value: (a.kind.value && ARRANGEMENT[a.kind.value]) || missing},
      {label: 'הרכב הנוכחי', value: this.vehicleName(a.vehicle.value?.catalogueId) ?? missing},
      {label: 'סוג הנעה', value: (a.propulsion.value && PROPULSION[a.propulsion.value as keyof typeof PROPULSION]) || missing},
      {label: 'השתתפות חודשית ברכב', value: participation ? `${Number(participation).toLocaleString('he-IL')} ₪` : missing},
    ];
  });
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
    this.needsCurrent.set(false);
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
    if (message.kind !== 'options' || !Array.isArray(keys)) return [];
    const byKey = new Map(this.catalog.options().map(o => [o.optionKey, o]));
    return keys.flatMap(key => {
      const option = typeof key === 'string' ? byKey.get(key) : undefined;
      return option ? [option] : [];
    });
  }

  openVehicles(cards: OptionItem[]): void {
    this.panelCards.set(cards);
  }

  private vehicleName(catalogueId: string | null | undefined): string | null {
    if (!catalogueId) return null;
    const option = this.catalog.options().find(o => o.catalogueId === catalogueId);
    return option ? optionLabel(option) : catalogueId;
  }

  runOf(message: {kind: string; payload: Record<string, unknown>}): string | null {
    const runId = message.payload['runId'];
    return RUN_KINDS.has(message.kind) && typeof runId === 'string' ? runId : null;
  }
}
