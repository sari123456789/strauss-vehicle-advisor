import {ChangeDetectionStrategy, Component, computed, inject, input, viewChild} from '@angular/core';
import {RouterLink, RouterLinkActive} from '@angular/router';
import {AuthStore} from '../../core/auth/auth.store';
import {SheetComponent} from '../ui/sheet/sheet.component';

interface NavLink {
  path: string;
  label: string;
}

/**
 * Site header: brand, primary navigation and sign-in action.
 * Under 1024px the navigation moves into an accessible drawer opened by a menu button.
 * `overlay` lays the header over the homepage hero from 1024px, as in the approved design.
 */
@Component({
  selector: 'dr-site-header',
  imports: [RouterLink, RouterLinkActive, SheetComponent],
  templateUrl: './site-header.component.html',
  styleUrl: './site-header.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {'[class.is-overlay]': "variant() === 'overlay'"},
})
export class SiteHeaderComponent {
  readonly variant = input<'default' | 'overlay'>('default');

  // Session state still comes from the local demo AuthStore; it is replaced by the server
  // session once the agreed auth contract (open item O12) is published.
  private readonly auth = inject(AuthStore);
  protected readonly signedIn = computed(() => !!this.auth.role());

  private readonly publicLinks: NavLink[] = [
    {path: '/how-it-works', label: 'איך זה עובד'},
    {path: '/catalog', label: 'השוואת רכבים'},
    {path: '/profile', label: 'הנתונים שלי'},
  ];
  private readonly signedInLinks: NavLink[] = [
    {path: '/advisor', label: 'היועץ שלי'},
    {path: '/history', label: 'ההשוואות שלי'},
  ];
  protected readonly links = computed(() => (this.signedIn() ? [...this.publicLinks, ...this.signedInLinks] : this.publicLinks));

  private readonly menu = viewChild.required<SheetComponent>('menu');

  protected openMenu(trigger: EventTarget | null): void {
    this.menu().open(trigger as HTMLElement | null);
  }

  protected closeMenu(): void {
    this.menu().close();
  }

  protected signOut(): void {
    this.auth.logout();
    this.closeMenu();
  }
}
