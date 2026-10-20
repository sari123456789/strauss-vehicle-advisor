import {inject, provideAppInitializer} from '@angular/core';
import {provideHttpClient, withInterceptors} from '@angular/common/http';
import {bootstrapApplication} from '@angular/platform-browser';
import {provideRouter, withHashLocation} from '@angular/router';
import {AppComponent} from './app/app.component';
import {routes} from './app/app.routes';
import {authInterceptor} from './app/core/auth/auth.interceptor';
import {SessionService} from './app/core/auth/session.service';

bootstrapApplication(AppComponent, {
  providers: [
    provideRouter(routes, withHashLocation()),
    provideHttpClient(withInterceptors([authInterceptor])),
    // Restore a session from the refresh cookie after a reload (never blocks on failure).
    provideAppInitializer(() => {
      void inject(SessionService).restore();
    }),
  ],
}).catch(console.error);
