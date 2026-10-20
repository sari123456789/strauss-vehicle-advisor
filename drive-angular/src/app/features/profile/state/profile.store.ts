import {Injectable, inject, signal} from '@angular/core';
import {BusinessApi} from '../../../core/api/business.api';
import {hebrewError} from '../../../core/api/problem';
import type {EmployeeProfileView} from '../../../core/api/types';

/**
 * The employee's actual profile (`GET /me/profile`), read-only in the UI: actual corrections
 * and hypothetical "what if" values are given to the advisor, which records them through the
 * Business tools with the right mode and version checks.
 */
@Injectable({providedIn: 'root'})
export class ProfileStore {
  private readonly api = inject(BusinessApi);

  readonly profile = signal<EmployeeProfileView | null>(null);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  async load(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      const envelope = await this.api.profile();
      this.profile.set(envelope.data ?? null);
    } catch (error) {
      this.error.set(hebrewError(error));
    } finally {
      this.loading.set(false);
    }
  }
}
