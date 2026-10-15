import {ChangeDetectionStrategy, Component, ElementRef, input, output, viewChild} from '@angular/core';

let nextId = 0;

/**
 * Accessible drawer/sheet on top of the native modal <dialog>:
 * focus is trapped and the rest of the page is inert while open, Esc closes,
 * focus returns to the element that opened it, and page scroll is locked.
 * Placement: bottom sheet under 600px, side drawer from the inline-start edge from 600px
 * (or forced with `placement`).
 */
@Component({
  selector: 'dr-sheet',
  templateUrl: './sheet.component.html',
  styleUrl: './sheet.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SheetComponent {
  readonly title = input.required<string>();
  readonly placement = input<'auto' | 'bottom' | 'side'>('auto');
  readonly closeLabel = input('סגירה');
  readonly closed = output<void>();

  protected readonly headingId = `dr-sheet-title-${++nextId}`;
  private readonly dialog = viewChild.required<ElementRef<HTMLDialogElement>>('dialog');
  private returnFocusTo: HTMLElement | null = null;

  open(trigger?: HTMLElement | null): void {
    const dialog = this.dialog().nativeElement;
    if (dialog.open) return;
    this.returnFocusTo = trigger ?? (document.activeElement as HTMLElement | null);
    dialog.showModal();
    document.documentElement.classList.add('scroll-locked');
  }

  close(): void {
    const dialog = this.dialog().nativeElement;
    if (dialog.open) dialog.close();
  }

  /** Runs for every way of closing: close(), Esc, or a form with method="dialog". */
  protected onDialogClose(): void {
    document.documentElement.classList.remove('scroll-locked');
    const target = this.returnFocusTo;
    this.returnFocusTo = null;
    if (target?.isConnected) target.focus();
    this.closed.emit();
  }

  /**
   * Keeps Tab / Shift+Tab cycling through the sheet's controls. A native modal dialog makes the page inert
   * but still lets focus leave to the browser chrome after the last control.
   */
  protected onDialogKeydown(event: KeyboardEvent): void {
    if (event.key !== 'Tab') return;
    const focusable = Array.from(
      this.dialog().nativeElement.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
      ),
    ).filter(el => el.getClientRects().length > 0);
    if (focusable.length === 0) return;
    // Move focus explicitly on every Tab: engines differ in what Tab reaches (Safari skips links by default).
    event.preventDefault();
    const index = focusable.indexOf(document.activeElement as HTMLElement);
    const count = focusable.length;
    const next = event.shiftKey ? (index <= 0 ? count - 1 : index - 1) : (index === -1 || index === count - 1 ? 0 : index + 1);
    focusable[next].focus();
  }

  /** A click whose target is the <dialog> itself landed on the backdrop, outside the panel. */
  protected onDialogClick(event: MouseEvent): void {
    if (event.target === this.dialog().nativeElement) this.close();
  }
}
