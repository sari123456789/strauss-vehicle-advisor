import {ChangeDetectionStrategy, Component, inject} from '@angular/core';
import {toSignal} from '@angular/core/rxjs-interop';
import {NavigationEnd, Router, RouterOutlet} from '@angular/router';
import {filter, map} from 'rxjs';
import {SiteFooterComponent} from './shared/layout/site-footer.component';
import {SiteHeaderComponent} from './shared/layout/site-header.component';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, SiteHeaderComponent, SiteFooterComponent],
  templateUrl: './app.component.html',
  styleUrl: './app.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppComponent {
  private readonly router = inject(Router);
  protected readonly isHome = toSignal(
    this.router.events.pipe(
      filter((event): event is NavigationEnd => event instanceof NavigationEnd),
      map(event => event.urlAfterRedirects === '/'),
    ),
    {initialValue: false},
  );

  /** In-page skip link; a plain #fragment would be treated as a route by the hash router. */
  protected skipToMain(event: Event, main: HTMLElement): void {
    event.preventDefault();
    main.focus();
  }
}
