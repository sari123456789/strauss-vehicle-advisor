import {HttpClient, HttpHeaders} from '@angular/common/http';
import {Injectable, InjectionToken, computed, inject, signal} from '@angular/core';
import {Observable, catchError, finalize, map, of, shareReplay, tap} from 'rxjs';

/** Base path of the real backend API. Served through the dev proxy (`proxy.conf.json`). */
export const API_V1_BASE_URL = new InjectionToken<string>('API_V1_BASE_URL', {
  providedIn: 'root',
  factory: () => '/api/v1',
});

export interface ApiActor {
  subjectId: string;
  roles: string[];
  displayName?: string | null;
}

interface SessionResponse {
  accessToken: string;
  tokenType: 'Bearer';
  expiresAt: string;
  actor: ApiActor;
}

/** Cookie-authenticated routes require this header (backend `require_csrf`). */
export const CSRF_HEADERS = new HttpHeaders({'X-CSRF-Protection': '1'});

/**
 * Minimal real-API session (smallest piece the full Angular integration can adopt):
 * - the access token lives in memory only (never in localStorage);
 * - the refresh token is the backend's HttpOnly cookie, used by `refresh()` to restore a session
 *   after a reload; cookie routes send the CSRF header.
 */
@Injectable({providedIn: 'root'})
export class ApiSession {
  private readonly http = inject(HttpClient);
  private readonly base = inject(API_V1_BASE_URL);

  private readonly token = signal<string | null>(null);
  readonly actor = signal<ApiActor | null>(null);
  readonly signedIn = computed(() => this.token() !== null);
  readonly isEmployee = computed(() => this.actor()?.roles.includes('employee') ?? false);
  private refreshing: Observable<boolean> | null = null;

  accessToken(): string | null {
    return this.token();
  }

  isAuthUrl(url: string): boolean {
    return url.startsWith(`${this.base}/auth/`);
  }

  isApiUrl(url: string): boolean {
    return url.startsWith(`${this.base}/`);
  }

  login(login: string, password: string): Observable<ApiActor> {
    return this.http
      .post<SessionResponse>(`${this.base}/auth/login`, {login, password}, {withCredentials: true})
      .pipe(map((session) => this.accept(session)));
  }

  /** Restores or renews the session from the refresh cookie; never throws. Shared while running. */
  refresh(): Observable<boolean> {
    this.refreshing ??= this.http
      .post<SessionResponse>(`${this.base}/auth/refresh`, null, {withCredentials: true, headers: CSRF_HEADERS})
      .pipe(
        map((session) => {
          this.accept(session);
          return true;
        }),
        catchError(() => {
          this.clear();
          return of(false);
        }),
        finalize(() => (this.refreshing = null)),
        shareReplay(1),
      );
    return this.refreshing;
  }

  logout(): Observable<void> {
    return this.http
      .post<void>(`${this.base}/auth/logout`, null, {withCredentials: true, headers: CSRF_HEADERS})
      .pipe(
        catchError(() => of(undefined)),
        tap(() => this.clear()),
      );
  }

  clear(): void {
    this.token.set(null);
    this.actor.set(null);
  }

  private accept(session: SessionResponse): ApiActor {
    this.token.set(session.accessToken);
    this.actor.set(session.actor);
    return session.actor;
  }
}
