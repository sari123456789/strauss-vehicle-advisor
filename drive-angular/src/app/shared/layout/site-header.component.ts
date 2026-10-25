import {ChangeDetectionStrategy, Component, computed, inject, input, viewChild} from '@angular/core';
import {Router, RouterLink, RouterLinkActive} from '@angular/router';
import {SessionService} from '../../core/auth/session.service';
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

  // Server session (bearer token in memory, refresh cookie); links follow the actor's role.
  private readonly session = inject(SessionService);
  private readonly router = inject(Router);
  protected readonly signedIn = this.session.signedIn;
  /** The signed-in person's name, shown beside the sign-out button. */
  protected readonly displayName = this.session.displayName;

  // Anonymous visitors see the approved design's links (protected pages lead to sign-in).
  private readonly publicLinks: NavLink[] = [
    {path: '/how-it-works', label: 'איך זה עובד'},
    {path: '/catalog', label: 'השוואת רכבים'},
  ];
  // Employees follow the three steps (shown under the header); the menu keeps only what is outside them.
  private readonly employeeLinks: NavLink[] = [
    {path: '/advisor', label: 'התחלה'},
    {path: '/history', label: 'ההשוואות שלי'},
    {path: '/requests', label: 'פנייה לנציג'},
  ];
  private readonly representativeLinks: NavLink[] = [
    {path: '/representative', label: 'מרכז הפניות'},
  ];
  protected readonly links = computed(() => {
    if (this.session.isRepresentative()) return this.representativeLinks;
    return this.session.isEmployee() ? this.employeeLinks : this.publicLinks;
  });

  private readonly menu = viewChild.required<SheetComponent>('menu');

  protected openMenu(trigger: EventTarget | null): void {
    this.menu().open(trigger as HTMLElement | null);
  }

  protected closeMenu(): void {
    this.menu().close();
  }

  protected async signOut(): Promise<void> {
    this.closeMenu();
    await this.session.logout();
    await this.router.navigateByUrl('/');
  }
}
