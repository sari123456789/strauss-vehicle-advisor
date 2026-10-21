import {ChangeDetectionStrategy, Component} from '@angular/core';
import {RouterLink} from '@angular/router';

@Component({
  selector: 'dr-site-footer',
  imports: [RouterLink],
  template: `
    <footer class="site-footer">
      <a class="brand" routerLink="/">
        <span class="brand__mark">DRIVE</span>
        <span class="brand__tag">יועץ הרכב שלך</span>
      </a>
      <a class="site-footer__link" routerLink="/representative">מסך נציג</a>
    </footer>
  `,
  styles: `
    .site-footer {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 16px 24px;
      padding-block: 24px max(24px, env(safe-area-inset-bottom));
      padding-inline: var(--gutter-inline-start) var(--gutter-inline-end);
      border-top: 1px solid var(--border);
      color: var(--text-muted);
      font-size: 13px;
    }
    .brand {
      display: flex;
      flex-direction: column;
      min-block-size: var(--tap);
      justify-content: center;
      direction: ltr;
      text-align: right;
      color: var(--accent);
    }
    .brand__mark { font-size: 22px; font-weight: 500; letter-spacing: 0.28em; margin-right: -0.28em; line-height: 1.1; }
    .brand__tag { direction: rtl; font-size: 11px; letter-spacing: 0.15em; color: var(--text-muted); margin-top: 4px; }
    .site-footer__link { display: inline-flex; align-items: center; min-block-size: var(--tap); padding-inline: 4px; }
    .site-footer__link:hover { color: var(--accent); }
  `,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SiteFooterComponent {}
