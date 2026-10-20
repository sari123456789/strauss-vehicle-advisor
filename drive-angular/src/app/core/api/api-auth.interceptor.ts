import {HttpErrorResponse, HttpInterceptorFn, HttpRequest} from '@angular/common/http';
import {inject} from '@angular/core';
import {catchError, switchMap, throwError} from 'rxjs';
import {ApiSession} from './api-session';

function withBearer(request: HttpRequest<unknown>, token: string | null): HttpRequest<unknown> {
  return token ? request.clone({setHeaders: {Authorization: `Bearer ${token}`}}) : request;
}

/**
 * Adds the in-memory bearer token to real-API requests (`/api/v1/...`, except the auth routes).
 * On a 401 it renews the session once through the refresh cookie and retries the request.
 * Demo/mock requests outside `/api/v1` are untouched.
 */
export const apiAuthInterceptor: HttpInterceptorFn = (request, next) => {
  const session = inject(ApiSession);
  if (!session.isApiUrl(request.url) || session.isAuthUrl(request.url)) {
    return next(request);
  }
  return next(withBearer(request, session.accessToken())).pipe(
    catchError((error: unknown) => {
      if (!(error instanceof HttpErrorResponse) || error.status !== 401) {
        return throwError(() => error);
      }
      return session.refresh().pipe(
        switchMap((renewed) =>
          renewed ? next(withBearer(request, session.accessToken())) : throwError(() => error),
        ),
      );
    }),
  );
};
