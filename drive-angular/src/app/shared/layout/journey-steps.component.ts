import {ChangeDetectionStrategy, Component, computed, input} from '@angular/core';
import {RouterLink} from '@angular/router';

interface Step {
  path: string;
  title: string;
  hint: string;
}

/**
 * The employee's path through the site in three steps: details in the chat, choosing cars, and
 * the monthly difference from today. Shown under the header on those pages.
 */
@Component({
  selector: 'dr-journey-steps',
  imports: [RouterLink],
  template: `
    <nav class="steps" aria-label="שלבי הבדיקה">
      <ol>
        @for (step of steps; track step.path; let i = $index) {
          <li [class.is-current]="i === current()" [class.is-done]="i < current()">
            <a [routerLink]="step.path" [attr.aria-current]="i === current() ? 'step' : null">
              <span class="steps__num">@if (i < current()) {✓} @else {{{ i + 1 }}}</span>
              <span class="steps__text"><b>{{ step.title }}</b><small>{{ step.hint }}</small></span>
            </a>
          </li>
        }
      </ol>
    </nav>
  `,
  styles: `
    .steps {
      padding: 14px var(--gutter-inline-end) 14px var(--gutter-inline-start);
      background: #fff;
      border-bottom: 1px solid #ececef;
    }
    ol {
      display: flex;
      gap: 8px;
      margin: 0 auto;
      padding: 0;
      max-inline-size: 960px;
      list-style: none;
    }
    li {
      flex: 1 1 0;
      position: relative;
    }
    li + li::before {
      content: '';
      position: absolute;
      inset-block-start: 19px;
      inset-inline-end: calc(100% - 4px);
      inline-size: 12px;
      border-top: 2px solid #e2e3e7;
    }
    a {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 8px 10px;
      border-radius: 12px;
      color: #6b6f76;
      text-decoration: none;
    }
    a:hover {
      background: #faf6f2;
    }
    .steps__num {
      display: grid;
      place-items: center;
      flex: 0 0 auto;
      inline-size: 30px;
      block-size: 30px;
      border-radius: 50%;
      border: 2px solid #d8d9de;
      font-weight: 700;
      font-size: 14px;
    }
    .steps__text {
      display: flex;
      flex-direction: column;
      line-height: 1.25;
    }
    .steps__text b {
      font-size: 15px;
    }
    .steps__text small {
      font-size: 12px;
    }
    .is-current a {
      background: #fbf1e8;
      color: #111;
    }
    .is-current .steps__num {
      border-color: #ae7546;
      background: #ae7546;
      color: #fff;
    }
    .is-done .steps__num {
      border-color: #ae7546;
      color: #ae7546;
    }
    @media (max-width: 700px) {
      .steps__text small {
        display: none;
      }
      .steps__text b {
        font-size: 13px;
      }
      a {
        flex-direction: column;
        gap: 4px;
        text-align: center;
      }
      li + li::before {
        display: none;
      }
    }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class JourneyStepsComponent {
  /** Index of the current step (0-2). */
  readonly current = input.required<number>();
  protected readonly steps: Step[] = [
    {path: '/advisor', title: 'הפרטים שלי', hint: 'משלימים בשיחה עם היועץ'},
    {path: '/catalog', title: 'בחירת רכבים', hint: 'בוחרים מה לבדוק'},
    {path: '/comparison', title: 'ההפרש החודשי', hint: 'כמה ישתנה לך בנטו'},
  ];
  protected readonly label = computed(() => this.steps[this.current()]?.title ?? '');
}
