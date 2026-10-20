import {InjectionToken} from '@angular/core';

/**
 * Base URL of the backend API. Same-origin by default: `ng serve` proxies `/api` to the local
 * backend (proxy.conf.json) and Vercel rewrites `/api/*` to the hosted API (vercel.json), so
 * the refresh cookie stays first-party. Override only for a cross-site deployment.
 */
export const API_BASE_URL = new InjectionToken<string>('API_BASE_URL', {
  providedIn: 'root',
  factory: () => '/api/v1',
});

export function newId(): string {
  return crypto.randomUUID();
}

/** The current calendar month as the server expects it (`YYYY-MM`). */
export function currentMonth(now = new Date()): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}
