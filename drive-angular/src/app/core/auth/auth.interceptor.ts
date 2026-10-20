import {HttpErrorResponse, HttpInterceptorFn, HttpRequest} from '@angular/common/http';
import {inject} from '@angular/core';
import {Router} from '@angular/router';
import {catchError, from, switchMap, throwError} from 'rxjs';
import {API_BASE_URL} from '../api/api-config';
import {SessionService} from './session.service';

function withToken(request: HttpRequest<unknown>, token: string | null): HttpRequest<unknown> {
  return token ? request.clone({setHeaders: {Authorization: `Bearer ${token}`}}) : request;
}

/**
 * Adds the bearer token to API calls. On `401` it refreshes the session once and retries the
 * request; when the refresh fails the user is sent to sign-in (no raw error is shown).
 */
export const authInterceptor: HttpInterceptorFn = (request, next) => {
  const base = inject(API_BASE_URL);
  if (!request.url.startsWith(base)) return next(request);
  const session = inject(SessionService);
  const router = inject(Router);
  return next(withToken(request, session.token())).pipe(
    catchError((error: unknown) => {
      if (!(error instanceof HttpErrorResponse) || error.status !== 401) return throwError(() => error);
      return from(session.refresh()).pipe(
        switchMap(ok => {
          if (!ok) {
            void router.navigateByUrl('/sign-in');
            return throwError(() => error);
          }
          return next(withToken(request, session.token()));
        }),
      );
    }),
  );
};
