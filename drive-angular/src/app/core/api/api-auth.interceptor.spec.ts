import {HttpClient, provideHttpClient, withInterceptors} from '@angular/common/http';
import {HttpTestingController, provideHttpClientTesting} from '@angular/common/http/testing';
import {TestBed} from '@angular/core/testing';
import {firstValueFrom} from 'rxjs';
import {apiAuthInterceptor} from './api-auth.interceptor';
import {ApiSession} from './api-session';

const session = (token: string) => ({
  accessToken: token,
  tokenType: 'Bearer',
  expiresAt: '2026-10-20T10:00:00Z',
  actor: {subjectId: 's-1', roles: ['employee']},
});

describe('apiAuthInterceptor and ApiSession', () => {
  let http: HttpClient;
  let backend: HttpTestingController;
  let api: ApiSession;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(withInterceptors([apiAuthInterceptor])), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpClient);
    backend = TestBed.inject(HttpTestingController);
    api = TestBed.inject(ApiSession);
  });

  afterEach(() => backend.verify());

  async function signIn(token = 't-1'): Promise<void> {
    const pending = firstValueFrom(api.login('employee1', 'secret'));
    const login = backend.expectOne('/api/v1/auth/login');
    expect(login.request.withCredentials).toBe(true);
    expect(login.request.headers.has('Authorization')).toBe(false);
    login.flush(session(token));
    await pending;
  }

  it('adds the bearer token to real API calls only', async () => {
    await signIn();
    expect(api.isEmployee()).toBe(true);
    http.get('/api/v1/me/profile').subscribe();
    expect(backend.expectOne('/api/v1/me/profile').request.headers.get('Authorization')).toBe('Bearer t-1');
    http.get('/api/vehicles').subscribe();
    expect(backend.expectOne('/api/vehicles').request.headers.has('Authorization')).toBe(false);
  });

  it('renews once through the refresh cookie with the CSRF header, then retries', async () => {
    await signIn('old');
    const result = firstValueFrom(http.get<{ok: boolean}>('/api/v1/me/profile'));
    backend.expectOne('/api/v1/me/profile').flush({}, {status: 401, statusText: 'x'});
    const refresh = backend.expectOne('/api/v1/auth/refresh');
    expect(refresh.request.headers.get('X-CSRF-Protection')).toBe('1');
    refresh.flush(session('new'));
    const retried = backend.expectOne('/api/v1/me/profile');
    expect(retried.request.headers.get('Authorization')).toBe('Bearer new');
    retried.flush({ok: true});
    expect(await result).toEqual({ok: true});
  });

  it('signs out when the refresh fails and surfaces the original 401', async () => {
    await signIn();
    const result = firstValueFrom(http.get('/api/v1/me/profile')).catch((e: {status: number}) => e.status);
    backend.expectOne('/api/v1/me/profile').flush({}, {status: 401, statusText: 'x'});
    backend.expectOne('/api/v1/auth/refresh').flush({}, {status: 401, statusText: 'x'});
    expect(await result).toBe(401);
    expect(api.signedIn()).toBe(false);
  });
});
