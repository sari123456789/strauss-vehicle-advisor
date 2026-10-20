import {ChangeDetectionStrategy, Component, OnInit, inject, signal} from '@angular/core';
import {RouterLink} from '@angular/router';
import {firstValueFrom} from 'rxjs';
import {ApiSession} from '../../core/api/api-session';
import {EmployeeApiClient, EmployeeProfileView, ProfileFieldView} from '../../core/api/employee-api.client';
import {FIELD_LABELS, displayValue} from './payslip-format';
import {PayslipImportComponent} from './payslip-import.component';
import {PayslipFieldPath} from './payslip.models';

type SectionState = 'loading' | 'signed-out' | 'ready' | 'error';

const PROFILE_KEYS: ReadonlyArray<[PayslipFieldPath, keyof EmployeeProfileView]> = [
  ['payroll.baseGross', 'baseGross'],
  ['payroll.commissionsGross', 'commissionsGross'],
  ['payroll.bonusGross', 'bonusGross'],
  ['payroll.pensionEmployeeRate', 'pensionEmployeeRate'],
  ['payroll.studyFundEmployeeRate', 'studyFundEmployeeRate'],
  ['payroll.creditPoints', 'creditPoints'],
];

/**
 * Profile-page entry point: the employee's REAL salary facts (read-only) plus the payslip import.
 * After an approved import the profile is reloaded from the server, so the page shows exactly
 * what Business stored.
 */
@Component({
  selector: 'dr-profile-payslip-section',
  imports: [PayslipImportComponent, RouterLink],
  template: `
    <section class="salary" aria-labelledby="salary-title" data-testid="real-profile">
      <h2 id="salary-title" class="salary__title">נתוני השכר השמורים בפרופיל</h2>
      @switch (state()) {
        @case ('loading') {
          <p class="salary__muted" role="status">טוען את הפרופיל…</p>
        }
        @case ('signed-out') {
          <p class="salary__muted">
            כדי לראות את נתוני השכר השמורים ולייבא אותם מתלוש, יש <a routerLink="/sign-in">להתחבר</a>.
          </p>
        }
        @case ('error') {
          <div role="alert" class="salary__alert">
            <p>לא הצלחתי לטעון את הפרופיל כרגע.</p>
            <button type="button" class="btn btn--outline" (click)="load()">לנסות שוב</button>
          </div>
        }
        @default {
          @if (profile(); as current) {
            <dl class="salary__list">
              @for (row of rows(current); track row.field) {
                <div class="salary__row" [attr.data-field]="row.field">
                  <dt>{{ labels[row.field] }}</dt>
                  <dd dir="ltr">{{ row.text }}</dd>
                </div>
              }
            </dl>
            @if (conversationId(); as conversation) {
              @if (importOpen()) {
                <dr-payslip-import
                  [conversationId]="conversation"
                  [profileVersion]="current.profileVersion"
                  (applied)="load()"
                  (profileStale)="load()"
                  (closed)="importOpen.set(false)"
                />
              } @else {
                <button type="button" class="btn btn--outline" (click)="importOpen.set(true)" data-testid="open-payslip-import">
                  עדכון נתוני השכר מתלוש (PDF)
                </button>
              }
            }
          }
        }
      }
    </section>
  `,
  styles: `
    :host { display: block; margin-block: 24px; }
    .salary { display: flex; flex-direction: column; gap: 16px; }
    .salary__title { margin: 0; font-size: 1.25rem; }
    .salary__muted { margin: 0; color: var(--text-muted); }
    .salary__alert { display: flex; flex-direction: column; gap: 8px; color: var(--danger); }
    .salary__alert p { margin: 0; }
    .salary__list { display: grid; gap: 0; margin: 0; }
    .salary__row {
      display: flex; justify-content: space-between; gap: 12px;
      padding: 10px 0; border-block-end: 1px solid var(--rule);
    }
    .salary__row dt { color: var(--text-soft); }
    .salary__row dd { margin: 0; font-variant-numeric: tabular-nums; font-weight: 600; }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ProfilePayslipSectionComponent implements OnInit {
  private readonly session = inject(ApiSession);
  private readonly api = inject(EmployeeApiClient);

  readonly state = signal<SectionState>('loading');
  readonly profile = signal<EmployeeProfileView | null>(null);
  readonly conversationId = signal<string | null>(null);
  readonly importOpen = signal(false);
  protected readonly labels = FIELD_LABELS;

  async ngOnInit(): Promise<void> {
    if (!this.session.signedIn()) {
      const restored = await firstValueFrom(this.session.refresh());
      if (!restored) {
        this.state.set('signed-out');
        return;
      }
    }
    await this.load();
  }

  async load(): Promise<void> {
    const actor = this.session.actor();
    if (!actor || !this.session.isEmployee()) {
      this.state.set('signed-out');
      return;
    }
    try {
      const [profile, conversation] = await Promise.all([
        firstValueFrom(this.api.profile()),
        this.conversationId() ?? firstValueFrom(this.api.workingConversation(actor.subjectId)),
      ]);
      this.profile.set(profile);
      this.conversationId.set(conversation);
      this.state.set('ready');
    } catch {
      this.state.set(this.session.signedIn() ? 'error' : 'signed-out');
    }
  }

  rows(profile: EmployeeProfileView): {field: PayslipFieldPath; text: string}[] {
    return PROFILE_KEYS.map(([field, key]) => {
      const entry = profile[key] as ProfileFieldView;
      const value = entry.status === 'missing' || entry.value === null ? null : String(entry.value);
      return {field, text: value === null ? 'לא הוזן' : displayValue(field, value)};
    });
  }
}
