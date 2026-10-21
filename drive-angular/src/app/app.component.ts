import {ChangeDetectionStrategy, Component, computed, inject} from '@angular/core';
import {toSignal} from '@angular/core/rxjs-interop';
import {NavigationEnd, Router, RouterLink, RouterOutlet} from '@angular/router';
import {SessionService} from './core/auth/session.service';
import {filter, map} from 'rxjs';
import {SiteFooterComponent} from './shared/layout/site-footer.component';
import {SiteHeaderComponent} from './shared/layout/site-header.component';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, SiteHeaderComponent, SiteFooterComponent],
  templateUrl: './app.component.html',
  styleUrl: './app.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppComponent {
  private readonly router = inject(Router);
  private readonly session = inject(SessionService);
  private readonly url = toSignal(
    this.router.events.pipe(
      filter((event): event is NavigationEnd => event instanceof NavigationEnd),
      map(event => event.urlAfterRedirects),
    ),
    {initialValue: ''},
  );
  protected readonly isHome = computed(() => this.url() === '/');
  /** The chat is a full workspace: no site footer under it. */
  protected readonly isChat = computed(() => this.url().startsWith('/advisor'));
  /** "Back to the chat" button: for a signed-in employee on every page except the chat itself. */
  protected readonly showChatButton = computed(
    () => this.session.isEmployee() && !this.url().startsWith('/advisor') && !this.url().startsWith('/sign-in'),
  );

  /** In-page skip link; a plain #fragment would be treated as a route by the hash router. */
  protected skipToMain(event: Event, main: HTMLElement): void {
    event.preventDefault();
    main.focus();
  }
}
