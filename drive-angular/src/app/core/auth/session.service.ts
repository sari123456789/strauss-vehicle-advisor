import {HttpBackend, HttpClient, HttpHeaders} from '@angular/common/http';
import {Injectable, computed, inject, signal} from '@angular/core';
import {firstValueFrom} from 'rxjs';
import {API_BASE_URL} from '../api/api-config';
import type {ActorView, SessionResponse} from '../api/types';

export const CSRF_HEADER = 'X-CSRF-Protection';

/**
 * Server session (architecture §22). The access token lives in memory only; the refresh token
 * is an HttpOnly cookie scoped to `/api/v1/auth`, sent with `withCredentials` plus the CSRF
 * header. A page reload restores the session through `/auth/refresh`.
 *
 * Uses `HttpBackend` (no interceptors) so auth calls never recurse through the 401 handler.
 */
@Injectable({providedIn: 'root'})
export class SessionService {
  private readonly http = new HttpClient(inject(HttpBackend));
  private readonly base = `${inject(API_BASE_URL)}/auth`;

  private readonly tokenState = signal<string | null>(null);
  private readonly actorState = signal<ActorView | null>(null);
  private refreshing: Promise<boolean> | null = null;
  private restored: Promise<void> | null = null;

  readonly actor = this.actorState.asReadonly();
  readonly signedIn = computed(() => this.actorState() !== null);
  readonly isEmployee = computed(() => this.actorState()?.roles.includes('employee') ?? false);
  readonly isRepresentative = computed(() => this.actorState()?.roles.includes('representative') ?? false);
  readonly displayName = computed(() => this.actorState()?.displayName ?? null);

  token(): string | null {
    return this.tokenState();
  }

  async login(login: string, password: string): Promise<ActorView> {
    const session = await firstValueFrom(
      this.http.post<SessionResponse>(`${this.base}/login`, {login, password}, {withCredentials: true}),
    );
    this.accept(session);
    return session.actor;
  }

  /** One refresh at a time; resolves false (and clears the session) when it fails. */
  refresh(): Promise<boolean> {
    this.refreshing ??= firstValueFrom(
      this.http.post<SessionResponse>(`${this.base}/refresh`, null, {
        withCredentials: true,
        headers: new HttpHeaders({[CSRF_HEADER]: '1'}),
      }),
    )
      .then(session => {
        this.accept(session);
        return true;
      })
      .catch(() => {
        this.clear();
        return false;
      })
      .finally(() => (this.refreshing = null));
    return this.refreshing;
  }

  /** At startup: silently restore a session from the refresh cookie (never throws). */
  restore(): Promise<void> {
    this.restored ??= this.refresh().then(() => undefined);
    return this.restored;
  }

  async logout(): Promise<void> {
    try {
      await firstValueFrom(
        this.http.post(`${this.base}/logout`, null, {
          withCredentials: true,
          headers: new HttpHeaders({[CSRF_HEADER]: '1'}),
        }),
      );
    } catch {
      // The local session is cleared anyway; the server session expires on its own.
    }
    this.clear();
  }

  clear(): void {
    this.tokenState.set(null);
    this.actorState.set(null);
  }

  private accept(session: SessionResponse): void {
    this.tokenState.set(session.accessToken);
    this.actorState.set(session.actor);
  }
}
