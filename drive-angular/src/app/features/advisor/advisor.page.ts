import {Component, OnDestroy, OnInit, inject} from '@angular/core';
import {ActivatedRoute, Router, RouterLink} from '@angular/router';
import {FormsModule} from '@angular/forms';
import {FIELD_STATUS} from '../../core/api/labels';
import {CarArtComponent} from '../../shared/ui/car-art.component';
import {MoneyPipe} from '../../shared/format/money.pipe';
import {PageHeadingComponent} from '../../shared/ui/page-heading.component';
import {ProfileStore} from '../profile/state/profile.store';
import {ConversationStore} from './state/conversation.store';

/** Message kinds that carry a Business comparison run the employee can open in detail. */
const RUN_KINDS = new Set(['comparison', 'history']);

@Component({
  standalone: true,
  imports: [RouterLink, FormsModule, CarArtComponent, PageHeadingComponent, MoneyPipe],
  templateUrl: './advisor.page.html',
})
export class AdvisorPage implements OnInit, OnDestroy {
  readonly store = inject(ConversationStore);
  readonly profile = inject(ProfileStore);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  readonly fieldStatus = FIELD_STATUS;
  text = '';

  async ngOnInit(): Promise<void> {
    void this.profile.load();
    const requested = this.route.snapshot.paramMap.get('conversationId');
    if (requested) await this.store.open(requested);
    else await this.store.ensureActive().catch(() => undefined);
    void this.store.loadConversations();
    this.store.watch();
  }

  ngOnDestroy(): void {
    this.store.unwatch();
  }

  send(): void {
    const text = this.text;
    this.text = '';
    void this.store.send(text).then(() => this.profile.load());
  }

  async startNew(): Promise<void> {
    await this.store.startNew();
    await this.router.navigate(['/advisor', this.store.active()?.conversationId]);
  }

  async switchTo(conversationId: string): Promise<void> {
    await this.router.navigate(['/advisor', conversationId]);
    await this.store.open(conversationId);
  }

  runOf(message: {kind: string; payload: Record<string, unknown>}): string | null {
    const runId = message.payload['runId'];
    return RUN_KINDS.has(message.kind) && typeof runId === 'string' ? runId : null;
  }
}
