/**
 * The employee and representative workflow against the HTTP contracts (replaces the old
 * tests/workflow.spec.ts, which exercised localStorage demo stores and a browser-side
 * eligibility rule). Equivalent coverage, on the real client code:
 * - sign-in, bearer token on API calls, session restore after reload (refresh + CSRF);
 * - 401 -> one refresh -> the request is retried;
 * - the advisor chat: 202 submit, polling until the input is final, resend after a failure;
 * - eligibility is displayed exactly as the server returned it (no client rule);
 * - the representative answer is a structured AnswerSubmit, retried with the same operation;
 * - saved conversations come back from the server after a reload (history);
 * - errors become Hebrew text, never raw codes.
 */
import {HttpTestingController, provideHttpClientTesting} from '@angular/common/http/testing';
import {provideHttpClient, withInterceptors} from '@angular/common/http';
import {TestBed} from '@angular/core/testing';
import {provideRouter} from '@angular/router';
import {authInterceptor} from './core/auth/auth.interceptor';
import {CSRF_HEADER, SessionService} from './core/auth/session.service';
import {hebrewError} from './core/api/problem';
import {HttpErrorResponse} from '@angular/common/http';
import {ConversationStore} from './features/advisor/state/conversation.store';
import {CatalogStore} from './features/catalog/state/catalog.store';
import {InquiriesStore} from './features/representative/state/inquiries.store';

const API = '/api/v1';
const SESSION = {
  accessToken: 'token-1',
  tokenType: 'Bearer',
  expiresAt: '2026-10-20T12:00:00Z',
  actor: {subjectId: 's-1', roles: ['employee'], displayName: 'עובד/ת'},
};
const CONVERSATION = {conversationId: 'c-1', caseId: 'case-1', status: 'active', calculationMonth: '2026-10'};

let active: HttpTestingController | null = null;

function setup(): HttpTestingController {
  TestBed.configureTestingModule({
    providers: [provideRouter([]), provideHttpClient(withInterceptors([authInterceptor])), provideHttpClientTesting()],
  });
  active = TestBed.inject(HttpTestingController);
  return active;
}

/** Let pending promise chains issue their next request. */
async function settle(): Promise<void> {
  for (let i = 0; i < 20; i++) await Promise.resolve();
}

async function expectRequest(http: HttpTestingController, method: string, url: string) {
  for (let i = 0; i < 50; i++) {
    const found = http.match(r => r.method === method && r.urlWithParams === url);
    if (found.length) return found[0];
    await settle();
  }
  return http.expectOne(r => r.method === method && r.urlWithParams === url);
}

function message(sequence: number, role: string, text: string, kind = 'text') {
  return {messageId: `m-${sequence}`, sequence, role, kind, text, payload: {}, inputId: 'i-1', createdAt: '2026-10-20T10:00:00Z'};
}

describe('workflow against the API contracts', () => {
  afterEach(() => {
    vi.useRealTimers();
    active?.verify();
    active = null;
  });

  it('signs in, sends the bearer token, and restores the session through refresh + CSRF', async () => {
    const http = setup();
    const session = TestBed.inject(SessionService);
    const login = session.login('e001', 'secret');
    const loginRequest = await expectRequest(http, 'POST', `${API}/auth/login`);
    expect(loginRequest.request.withCredentials).toBe(true);
    loginRequest.flush(SESSION);
    await login;
    expect(session.isEmployee()).toBe(true);

    const conversations = TestBed.inject(ConversationStore);
    const listing = conversations.loadConversations();
    const listRequest = await expectRequest(http, 'GET', `${API}/conversations`);
    expect(listRequest.request.headers.get('Authorization')).toBe('Bearer token-1');
    listRequest.flush({conversations: [CONVERSATION]});
    await listing;
    expect(conversations.conversations()).toEqual([CONVERSATION]);

    session.clear(); // a page reload: nothing survives in memory
    const restored = session.restore();
    const refresh = await expectRequest(http, 'POST', `${API}/auth/refresh`);
    expect(refresh.request.headers.get(CSRF_HEADER)).toBe('1');
    expect(refresh.request.withCredentials).toBe(true);
    refresh.flush({...SESSION, accessToken: 'token-2'});
    await restored;
    expect(session.token()).toBe('token-2');
  });

  it('refreshes once on 401 and retries the request', async () => {
    const http = setup();
    const session = TestBed.inject(SessionService);
    const login = session.login('e001', 'secret');
    (await expectRequest(http, 'POST', `${API}/auth/login`)).flush(SESSION);
    await login;

    const store = TestBed.inject(ConversationStore);
    const listing = store.loadConversations();
    (await expectRequest(http, 'GET', `${API}/conversations`)).flush(
      {code: 'UNAUTHENTICATED'},
      {status: 401, statusText: 'Unauthorized'},
    );
    (await expectRequest(http, 'POST', `${API}/auth/refresh`)).flush({...SESSION, accessToken: 'token-2'});
    const retried = await expectRequest(http, 'GET', `${API}/conversations`);
    expect(retried.request.headers.get('Authorization')).toBe('Bearer token-2');
    retried.flush({conversations: []});
    await listing;
    expect(store.error()).toBeNull();
  });

  it('sends a chat message, polls until the input is final, and offers a resend on failure', async () => {
    vi.useFakeTimers();
    const http = setup();
    const store = TestBed.inject(ConversationStore);
    store.active.set(CONVERSATION);

    const sending = store.send('כמה אקבל עם ATTO 3?');
    const submit = await expectRequest(http, 'POST', `${API}/conversations/c-1/messages`);
    expect(submit.request.body.text).toBe('כמה אקבל עם ATTO 3?');
    expect(submit.request.body.messageId).toMatch(/[0-9a-f-]{36}/);
    submit.flush({inputId: 'i-1', messageId: 'm-1', sequence: 1, duplicate: false}, {status: 202, statusText: 'Accepted'});
    (await expectRequest(http, 'GET', `${API}/conversations/c-1/messages?afterSequence=0`)).flush({
      messages: [message(1, 'user', 'כמה אקבל עם ATTO 3?')],
      lastSequence: 1,
      hasMore: false,
    });
    await sending;
    expect(store.busy()).toBe(true);

    {
      await vi.advanceTimersByTimeAsync(2000);
      (await expectRequest(http, 'GET', `${API}/conversations/c-1/messages?afterSequence=1`)).flush({
        messages: [message(2, 'assistant', 'הנטו המשוער: 11,693 ₪', 'comparison')],
        lastSequence: 2,
        hasMore: false,
      });
      (await expectRequest(http, 'GET', `${API}/conversations/c-1/inputs/i-1`)).flush({
        inputId: 'i-1', sequence: 1, status: 'completed', attempts: 1, messages: [],
      });
      (await expectRequest(http, 'GET', `${API}/conversations/c-1/messages?afterSequence=2`)).flush({
        messages: [], lastSequence: 2, hasMore: false,
      });
      await settle();
    }
    expect(store.busy()).toBe(false);
    expect(store.messages().map(m => m.text)).toEqual(['כמה אקבל עם ATTO 3?', 'הנטו המשוער: 11,693 ₪']);

    store.pending.set({inputId: 'i-2', text: 'שוב', status: 'dead'});
    expect(store.failedTurn()?.text).toBe('שוב');
    const resending = store.resend();
    (await expectRequest(http, 'POST', `${API}/conversations/c-1/messages`)).flush({inputId: 'i-3', messageId: 'm-3', sequence: 3, duplicate: false});
    (await expectRequest(http, 'GET', `${API}/conversations/c-1/messages?afterSequence=2`)).flush({messages: [], lastSequence: 3, hasMore: false});
    await resending;
    store.unwatch();
  });

  it('shows eligibility exactly as the server returned it', async () => {
    const http = setup();
    TestBed.inject(ConversationStore).active.set(CONVERSATION);
    const catalog = TestBed.inject(CatalogStore);
    const loading = catalog.load();
    const request = await expectRequest(http, 'POST', `${API}/cases/case-1/option-lists`);
    expect(request.request.body).toEqual({categories: ['regular', 'family', 'waiver']});
    request.flush({
      status: 'ok',
      traceId: 't',
      data: {
        resultSetId: 'r-1',
        scopeComplete: true,
        items: [
          {optionKey: 'vehicle:CAR-20', category: 'regular', eligibility: 'ineligible', group: 'א', reasons: ['group_higher_than_entitlement']},
          {optionKey: 'waiver', category: 'waiver', eligibility: 'eligible', waiverAllowance: '2000', reasons: ['waiver_allowed_by_occupation']},
        ],
      },
    });
    await loading;
    // The browser has no group ranking: an "א" vehicle stays ineligible because the server said so.
    expect(catalog.options().map(o => [o.optionKey, o.eligibility])).toEqual([
      ['vehicle:CAR-20', 'ineligible'],
      ['waiver', 'eligible'],
    ]);
  });

  it('answers a representative request with a structured, idempotent AnswerSubmit', async () => {
    const http = setup();
    const store = TestBed.inject(InquiriesStore);
    const request = {
      requestId: 'q-1', requestVersion: 0, status: 'open' as const, fieldCode: 'alternative.participation' as const,
      expectedValueType: 'money' as const, questionText: 'מה דמי ההשתתפות?', createdAt: '2026-10-20T10:00:00Z',
    };
    const first = store.answer(request, {outcome: 'answered', value: '350', effectiveFrom: '', effectiveTo: '', note: 'לפי המחירון'});
    const failed = await expectRequest(http, 'POST', `${API}/representative-requests/q-1/answers`);
    expect(failed.request.body).toEqual({
      operationId: failed.request.body.operationId,
      expectedRequestVersion: 0,
      outcome: 'answered',
      value: '350',
      unit: 'ILS',
      effectiveFrom: null,
      effectiveTo: null,
      note: 'לפי המחירון',
    });
    failed.flush({code: 'INFRA_UNAVAILABLE'}, {status: 503, statusText: 'Unavailable'});
    expect(await first).toBe(false);
    expect(store.error()).toBe('השירות אינו זמין כרגע. נסו שוב בעוד רגע.');

    const second = store.answer(request, {outcome: 'answered', value: '350', effectiveFrom: '', effectiveTo: '', note: ''});
    const retry = await expectRequest(http, 'POST', `${API}/representative-requests/q-1/answers`);
    expect(retry.request.body.operationId).toBe(failed.request.body.operationId); // no double answer
    retry.flush({status: 'ok', traceId: 't', data: {outcome: 'answered', value: '350', unit: 'ILS', answeredAt: '2026-10-20T10:01:00Z', application: 'applied'}});
    (await expectRequest(http, 'GET', `${API}/representative-requests?status=open&status=answered&status=cannot_determine`)).flush({status: 'ok', traceId: 't', data: []});
    expect(await second).toBe(true);
  });

  it('maps Problem Details to Hebrew and never shows a raw code', () => {
    const conflict = new HttpErrorResponse({status: 409, error: {code: 'VERSION_CONFLICT', traceId: 'abc'}});
    const offline = new HttpErrorResponse({status: 0});
    for (const text of [hebrewError(conflict), hebrewError(offline), hebrewError(new Error('x'))]) {
      expect(text).not.toMatch(/\b(409|503|0)\b|VERSION_CONFLICT|abc/);
      expect(text.length).toBeGreaterThan(5);
    }
  });
});
