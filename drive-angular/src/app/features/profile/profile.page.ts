import {Component, OnInit, computed, inject, signal} from '@angular/core';
import {RouterLink} from '@angular/router';
import {FIELD_STATUS} from '../../core/api/labels';
import {hebrewError} from '../../core/api/problem';
import {SessionService} from '../../core/auth/session.service';
import {ConversationStore} from '../advisor/state/conversation.store';
import {PayslipImportComponent} from '../payslip/payslip-import.component';
import {MoneyPipe} from '../../shared/format/money.pipe';
import {PageHeadingComponent} from '../../shared/ui/page-heading.component';
import {ProfileStore} from './state/profile.store';
import {profileRows} from './profile-rows';

/**
 * The saved EmployeeProfile, read-only: each field shows its value, status and source.
 * Corrections and "what if" values are given in the advisor conversation, which records them
 * as actual updates (with version checks) or as hypothetical values of that conversation.
 * The salary facts can also be imported from a payslip PDF: the employee reviews the draft and
 * explicitly approves it, then the profile is reloaded from the server.
 */
@Component({
  standalone: true,
  imports: [RouterLink, PageHeadingComponent, MoneyPipe, PayslipImportComponent],
  templateUrl: './profile.page.html',
})
export class ProfilePage implements OnInit {
  readonly store = inject(ProfileStore);
  readonly session = inject(SessionService);
  private readonly conversations = inject(ConversationStore);
  readonly fieldStatus = FIELD_STATUS;

  /** The conversation the import belongs to: the employee's active advisor conversation. */
  readonly importConversation = signal<string | null>(null);
  readonly importOpening = signal(false);
  readonly importError = signal<string | null>(null);

  readonly rows = computed(() => profileRows(this.store.profile()));

  ngOnInit(): void {
    void this.store.load();
  }

  /** Opens the payslip import inside the employee's active conversation (reopened or started). */
  async openImport(): Promise<void> {
    if (this.importOpening()) return;
    this.importOpening.set(true);
    this.importError.set(null);
    try {
      const conversation = await this.conversations.ensureActive();
      this.importConversation.set(conversation.conversationId);
    } catch (error) {
      this.importError.set(hebrewError(error));
    } finally {
      this.importOpening.set(false);
    }
  }

  closeImport(): void {
    this.importConversation.set(null);
  }
}
