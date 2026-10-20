import {HttpEventType} from '@angular/common/http';
import {Injectable, computed, inject, signal} from '@angular/core';
import {firstValueFrom} from 'rxjs';
import {PayslipApiClient} from './payslip-api.client';
import {PayslipUiError, RESTART_CODES, toPayslipError} from './payslip-errors';
import {checkPayslipFile, parseUserInput} from './payslip-format';
import {
  APPROVABLE_STATUSES,
  PayslipApprovalResult,
  PayslipDraft,
  PayslipDraftField,
  PayslipFieldPath,
} from './payslip.models';

export type PayslipPhase = 'idle' | 'uploading' | 'extracting' | 'review' | 'saving' | 'approving' | 'done';

/**
 * State of ONE payslip import (provided per component instance, so the profile page and the chat
 * reuse the same workflow independently). Nothing reaches the profile before `approve()`.
 */
@Injectable()
export class PayslipImportStore {
  private readonly api = inject(PayslipApiClient);

  readonly phase = signal<PayslipPhase>('idle');
  readonly uploadPercent = signal(0);
  readonly draft = signal<PayslipDraft | null>(null);
  readonly error = signal<PayslipUiError | null>(null);
  readonly selected = signal<ReadonlySet<PayslipFieldPath>>(new Set());
  readonly result = signal<PayslipApprovalResult | null>(null);
  /** Set when approval failed because the profile changed; the host should reload it. */
  readonly profileStale = signal(false);

  private conversationId = '';
  private readonly profileVersion = signal<number | null>(null);
  /** Same click retried with the same draft version and selection -> same operation ID. */
  private pendingOperation: {key: string; id: string} | null = null;

  readonly fields = computed(() => this.draft()?.fields ?? []);
  readonly busy = computed(() => ['uploading', 'extracting', 'saving', 'approving'].includes(this.phase()));
  readonly canApprove = computed(() => {
    const chosen = this.fields().filter((f) => this.selected().has(f.field));
    return (
      this.phase() === 'review' &&
      this.profileVersion() !== null &&
      chosen.length > 0 &&
      chosen.every(isApprovable)
    );
  });

  configure(conversationId: string, profileVersion: number | null): void {
    this.conversationId = conversationId;
    if (profileVersion !== this.profileVersion()) {
      this.profileVersion.set(profileVersion);
      this.profileStale.set(false);
    }
  }

  async upload(file: File): Promise<void> {
    if (this.busy()) return;
    const check = checkPayslipFile(file);
    if (!check.ok) {
      this.error.set(toPayslipError(check.code));
      return;
    }
    this.reset();
    this.phase.set('uploading');
    await new Promise<void>((resolve) => {
      this.api.upload(this.conversationId, file).subscribe({
        next: (event) => {
          if (event.type === HttpEventType.UploadProgress) {
            const percent = event.total ? Math.round((100 * event.loaded) / event.total) : 0;
            this.uploadPercent.set(percent);
            if (event.total && event.loaded >= event.total) this.phase.set('extracting');
          } else if (event.type === HttpEventType.Response && event.body) {
            this.showDraft(event.body);
          }
        },
        error: (error: unknown) => {
          this.phase.set('idle');
          this.error.set(toPayslipError(error));
          resolve();
        },
        complete: () => resolve(),
      });
    });
  }

  toggle(field: PayslipFieldPath): void {
    const target = this.fields().find((f) => f.field === field);
    if (!target || !isApprovable(target)) return;
    const next = new Set(this.selected());
    if (next.has(field)) next.delete(field);
    else next.add(field);
    this.selected.set(next);
  }

  /** Typed correction (rates as percent). Returns a Hebrew message for invalid input. */
  async correct(field: PayslipFieldPath, raw: string): Promise<string | null> {
    const parsed = parseUserInput(field, raw);
    if (!parsed.ok) return parsed.message;
    await this.correctValue(field, parsed.value);
    return null;
  }

  /** Correction with a contract value, e.g. one of the field's `alternatives`. */
  async correctValue(field: PayslipFieldPath, value: string): Promise<void> {
    const draft = this.draft();
    if (!draft || this.phase() !== 'review') return;
    this.phase.set('saving');
    this.error.set(null);
    try {
      const updated = await firstValueFrom(
        this.api.correct(draft.draftId, {expectedDraftVersion: draft.version, corrections: [{field, value}]}),
      );
      const selection = new Set(this.selected());
      selection.add(field);
      this.draft.set(updated);
      this.selected.set(selection);
      this.phase.set('review');
    } catch (error) {
      await this.fail(error);
    }
  }

  async approve(): Promise<void> {
    const draft = this.draft();
    const profileVersion = this.profileVersion();
    if (!draft || !this.canApprove() || profileVersion === null) return;
    const fields = this.fields()
      .filter((f) => this.selected().has(f.field))
      .map((f) => f.field);
    const key = `${draft.draftId}|${draft.version}|${profileVersion}|${fields.join(',')}`;
    if (this.pendingOperation?.key !== key) {
      this.pendingOperation = {key, id: crypto.randomUUID()};
    }
    this.phase.set('approving');
    this.error.set(null);
    try {
      const result = await firstValueFrom(
        this.api.approve(draft.draftId, {
          expectedDraftVersion: draft.version,
          expectedProfileVersion: profileVersion,
          operationId: this.pendingOperation.id,
          fields,
        }),
      );
      this.pendingOperation = null;
      this.result.set(result);
      this.phase.set('done');
    } catch (error) {
      await this.fail(error);
    }
  }

  async cancel(): Promise<void> {
    const draft = this.draft();
    this.reset();
    if (draft && draft.status !== 'applied') {
      try {
        await firstValueFrom(this.api.cancel(draft.draftId));
      } catch {
        // The draft expires on its own; nothing to show the employee.
      }
    }
  }

  dismissError(): void {
    this.error.set(null);
  }

  private showDraft(draft: PayslipDraft): void {
    this.draft.set(draft);
    this.selected.set(
      new Set(draft.fields.filter((f) => f.status === 'extracted' || f.status === 'corrected').map((f) => f.field)),
    );
    this.phase.set(draft.status === 'applied' ? 'done' : 'review');
  }

  private async fail(error: unknown): Promise<void> {
    const ui = toPayslipError(error);
    this.error.set(ui);
    if (RESTART_CODES.has(ui.code)) {
      this.draft.set(null);
      this.selected.set(new Set());
      this.phase.set('idle');
      return;
    }
    if (ui.code === 'PROFILE_VERSION_CONFLICT') this.profileStale.set(true);
    const draft = this.draft();
    if (draft && (ui.code === 'DRAFT_VERSION_CONFLICT' || ui.code === 'DRAFT_ALREADY_APPLIED')) {
      try {
        this.showDraft(await firstValueFrom(this.api.get(draft.draftId)));
        return;
      } catch {
        // Keep the current view; the error message already explains what happened.
      }
    }
    this.phase.set(draft ? 'review' : 'idle');
  }

  private reset(): void {
    this.draft.set(null);
    this.result.set(null);
    this.error.set(null);
    this.selected.set(new Set());
    this.uploadPercent.set(0);
    this.pendingOperation = null;
    this.phase.set('idle');
  }
}

export function isApprovable(field: PayslipDraftField): boolean {
  return APPROVABLE_STATUSES.has(field.status) && field.value !== null;
}
