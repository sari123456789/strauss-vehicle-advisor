import {Component, inject, signal} from '@angular/core';
import {FormsModule} from '@angular/forms';
import {ActivatedRoute, Router} from '@angular/router';
import {HttpErrorResponse} from '@angular/common/http';
import {hebrewError} from '../../core/api/problem';
import {SessionService} from '../../core/auth/session.service';
import {CarArtComponent} from '../../shared/ui/car-art.component';
import {PageHeadingComponent} from '../../shared/ui/page-heading.component';

@Component({standalone: true, imports: [FormsModule, CarArtComponent, PageHeadingComponent], templateUrl: './sign-in.page.html'})
export class SignInPage {
  private readonly session = inject(SessionService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  login = '';
  password = '';
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);

  async submit(): Promise<void> {
    if (this.busy() || !this.login.trim() || !this.password) return;
    this.busy.set(true);
    this.error.set(null);
    try {
      const actor = await this.session.login(this.login.trim(), this.password);
      this.password = '';
      const returnUrl = this.route.snapshot.queryParamMap.get('returnUrl');
      const home = actor.roles.includes('representative') ? '/representative' : '/advisor';
      await this.router.navigateByUrl(returnUrl && returnUrl.startsWith('/') ? returnUrl : home);
    } catch (error) {
      this.error.set(
        error instanceof HttpErrorResponse && error.status === 401
          ? 'שם המשתמש או הסיסמה אינם נכונים.'
          : error instanceof HttpErrorResponse && error.status === 429
            ? 'היו יותר מדי ניסיונות כניסה שגויים, ולכן הכניסה נחסמה זמנית. נסו שוב בעוד 15 דקות.'
            : hebrewError(error),
      );
    } finally {
      this.busy.set(false);
    }
  }
}
