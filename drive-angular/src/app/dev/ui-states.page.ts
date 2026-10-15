import {ChangeDetectionStrategy, Component, signal} from '@angular/core';
import {SheetComponent} from '../shared/ui/sheet/sheet.component';
import {EmptyStateComponent} from '../shared/ui/states/empty-state.component';
import {ErrorStateComponent} from '../shared/ui/states/error-state.component';
import {LoadingStateComponent} from '../shared/ui/states/loading-state.component';

/**
 * DEVELOPMENT ONLY (registered through dev-routes.development.ts; absent from production builds).
 * Renders the shared states and sheets with long Hebrew content for responsive/accessibility checks.
 * Contains no business data.
 */
@Component({
  imports: [LoadingStateComponent, ErrorStateComponent, EmptyStateComponent, SheetComponent],
  template: `
    <section class="dev-page" aria-labelledby="dev-title">
      <h1 id="dev-title">רכיבי מצב משותפים</h1>

      <h2>טעינה</h2>
      <dr-loading-state label="טוען את הנתונים…" [lines]="4" />

      <h2>שגיאה</h2>
      <dr-error-state (retry)="retries.set(retries() + 1)" />
      <p class="dev-note" aria-live="polite">ניסיונות חוזרים: {{ retries() }}</p>

      <h2>שגיאה עם טקסט ארוך</h2>
      <dr-error-state [message]="longText" [retryable]="false" />

      <h2>מצב ריק</h2>
      <dr-empty-state title="עוד אין השוואות שמורות" message="השוואה שתשמרו תופיע כאן.">
        <button type="button" class="btn btn--primary">התחלת שיחה</button>
      </dr-empty-state>

      <h2>מגירות</h2>
      <div class="dev-actions">
        <button #autoTrigger type="button" class="btn btn--outline" (click)="autoSheet.open(autoTrigger)">פרטי התיק</button>
        <button #bottomTrigger type="button" class="btn btn--outline" (click)="bottomSheet.open(bottomTrigger)">היסטוריית שיחות</button>
      </div>
    </section>

    <dr-sheet #autoSheet title="פרטי התיק">
      <p>{{ longText }}</p>
      <button type="button" class="btn btn--primary" (click)="autoSheet.close()">סגירה</button>
    </dr-sheet>
    <dr-sheet #bottomSheet title="היסטוריית שיחות עם כותרת ארוכה מאוד שצריכה להישבר בצורה נקייה" placement="bottom">
      @for (n of paragraphs; track n) {
        <p>{{ longText }}</p>
      }
    </dr-sheet>
  `,
  styles: `
    .dev-page { max-width: 960px; margin: 0 auto; padding: 32px var(--gutter-inline-start) 48px var(--gutter-inline-end); }
    h2 { margin: 32px 0 12px; font-size: 18px; }
    .dev-actions { display: flex; flex-wrap: wrap; gap: 12px; }
    .dev-note { margin-top: 8px; }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class UiStatesPage {
  protected readonly retries = signal(0);
  protected readonly paragraphs = [1, 2, 3, 4, 5, 6];
  protected readonly longText =
    'זוהי הודעה ארוכה במיוחד שנועדה לבדוק שבירת שורות בעברית במסכים צרים, כולל מילה ארוכה במיוחד ללא רווחים ' +
    'כמוזוהימילהארוכהמאודשלאאמורהלגרוםלגלילהאופקיתבשוםמצבובשוםרוחבמסךגםכשאיןבהרווחיםבכלל ' +
    'ומספרים כמו 1,234,567.89 וכתובת https://example.invalid/very/long/path/that/should/wrap/cleanly/in/rtl/text.';
}
