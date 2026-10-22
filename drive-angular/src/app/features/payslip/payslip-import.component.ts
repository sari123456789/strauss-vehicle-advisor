import {ChangeDetectionStrategy, Component, effect, inject, input, output, signal, untracked} from '@angular/core';
import {
  DOCUMENT_ISSUE_LABELS,
  FIELD_LABELS,
  ISSUE_LABELS,
  STATUS_LABELS,
  displayValue,
  editableValue,
} from './payslip-format';
import {PayslipImportStore, isApprovable} from './payslip-import.store';
import {PayslipApprovalResult, PayslipDraftField, PayslipFieldPath, RATE_FIELDS} from './payslip.models';

/**
 * The ONE payslip-import workflow: upload -> extraction -> review/correction -> explicit
 * approval. Hosted by the chat's details panel;
 * the host passes the conversation and the current profile version and refreshes the profile on
 * `applied`. Nothing is written to the profile before the employee presses the approve button.
 */
@Component({
  selector: 'dr-payslip-import',
  templateUrl: './payslip-import.component.html',
  styleUrl: './payslip-import.component.scss',
  providers: [PayslipImportStore],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PayslipImportComponent {
  readonly store = inject(PayslipImportStore);

  readonly conversationId = input.required<string>();
  /** Latest shared profile version the employee sees (from the profile/context route). */
  readonly profileVersion = input<number | null>(null);
  /** Compact layout for the chat panel. */
  readonly compact = input(false);

  readonly applied = output<PayslipApprovalResult>();
  /** The profile changed meanwhile; the host should reload it (and pass the new version). */
  readonly profileStale = output<void>();
  readonly closed = output<void>();

  readonly editing = signal<PayslipFieldPath | null>(null);
  readonly editText = signal('');
  readonly editMessage = signal<string | null>(null);

  protected readonly fieldLabels = FIELD_LABELS;
  protected readonly statusLabels = STATUS_LABELS;
  protected readonly issueLabels = ISSUE_LABELS;
  protected readonly documentIssueLabels = DOCUMENT_ISSUE_LABELS;
  protected readonly display = displayValue;
  protected readonly approvable = isApprovable;

  constructor() {
    effect(() => this.store.configure(this.conversationId(), this.profileVersion()));
    effect(() => {
      const result = this.store.result();
      if (result) untracked(() => this.applied.emit(result));
    });
    effect(() => {
      if (this.store.profileStale()) untracked(() => this.profileStale.emit());
    });
  }

  onFile(event: Event): void {
    const element = event.target as HTMLInputElement;
    const file = element.files?.[0];
    element.value = '';
    if (file) void this.store.upload(file);
  }

  isRate(field: PayslipFieldPath): boolean {
    return RATE_FIELDS.has(field);
  }

  startEdit(field: PayslipDraftField): void {
    this.editing.set(field.field);
    this.editText.set(editableValue(field.field, field.value ?? field.alternatives[0] ?? null));
    this.editMessage.set(null);
  }

  async saveEdit(field: PayslipFieldPath): Promise<void> {
    const message = await this.store.correct(field, this.editText());
    this.editMessage.set(message);
    if (message === null && !this.store.error()) this.editing.set(null);
  }

  async choose(field: PayslipFieldPath, value: string): Promise<void> {
    await this.store.correctValue(field, value);
  }

  async cancel(): Promise<void> {
    await this.store.cancel();
    this.closed.emit();
  }

  selectedCount(): number {
    return this.store.selected().size;
  }
}
