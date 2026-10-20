import {provideHttpClient} from '@angular/common/http';
import {HttpTestingController, provideHttpClientTesting} from '@angular/common/http/testing';
import {TestBed} from '@angular/core/testing';
import {PayslipImportStore} from './payslip-import.store';
import {PayslipDraft} from './payslip.models';
import {draft} from './payslip.testing';

const BASE = '/api/v1';

function pdf(size = 1000): File {
  return new File([new Uint8Array(size)], 'payslip.pdf', {type: 'application/pdf'});
}

describe('PayslipImportStore', () => {
  let store: PayslipImportStore;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [PayslipImportStore, provideHttpClient(), provideHttpClientTesting()],
    });
    store = TestBed.inject(PayslipImportStore);
    http = TestBed.inject(HttpTestingController);
    store.configure('c-1', 2);
  });

  afterEach(() => http.verify());

  async function uploaded(body: PayslipDraft = draft()): Promise<void> {
    const pending = store.upload(pdf());
    const request = http.expectOne(`${BASE}/conversations/c-1/payslip-imports`);
    expect(request.request.method).toBe('POST');
    expect(request.request.headers.get('Content-Type')).toBe('application/pdf');
    request.flush(body, {status: 201, statusText: 'Created'});
    await pending;
  }

  it('rejects non-PDF and oversized files without any request', async () => {
    await store.upload(new File(['x'], 'photo.png', {type: 'image/png'}));
    expect(store.error()?.code).toBe('NOT_A_PDF');
    await store.upload(pdf(3 * 1024 * 1024));
    expect(store.error()?.code).toBe('FILE_TOO_LARGE');
    http.expectNone(() => true);
  });

  it('shows the draft for review and preselects only extracted fields', async () => {
    await uploaded();
    expect(store.phase()).toBe('review');
    expect([...store.selected()]).toEqual(['payroll.baseGross']);
    store.toggle('payroll.bonusGross'); // conflict: not selectable
    store.toggle('payroll.creditPoints'); // missing: not selectable
    expect([...store.selected()]).toEqual(['payroll.baseGross']);
    expect(store.canApprove()).toBe(true);
  });

  it('maps upload problems to Hebrew messages', async () => {
    const pending = store.upload(pdf());
    http
      .expectOne(`${BASE}/conversations/c-1/payslip-imports`)
      .flush({code: 'INPUT_OUT_OF_SUPPORTED_RANGE', details: {payslipCode: 'ENCRYPTED_PDF'}}, {status: 422, statusText: 'x'});
    await pending;
    expect(store.phase()).toBe('idle');
    expect(store.error()?.message).toContain('סיסמה');
  });

  it('validates corrections locally, then sends rates as fractions with the draft version', async () => {
    await uploaded();
    expect(await store.correct('payroll.pensionEmployeeRate', '250')).toContain('אחוז');
    http.expectNone(`${BASE}/payslip-imports/d-1`);

    const pending = store.correct('payroll.pensionEmployeeRate', '6.5');
    const request = http.expectOne(`${BASE}/payslip-imports/d-1`);
    expect(request.request.method).toBe('PATCH');
    expect(request.request.body).toEqual({
      expectedDraftVersion: 1,
      corrections: [{field: 'payroll.pensionEmployeeRate', value: '0.065'}],
    });
    const corrected = draft({version: 2});
    corrected.fields[2] = {...corrected.fields[2], status: 'corrected', value: '0.065'};
    request.flush(corrected);
    expect(await pending).toBeNull();
    expect(store.draft()?.version).toBe(2);
    expect(store.selected().has('payroll.pensionEmployeeRate')).toBe(true);
  });

  it('approves only after the explicit action, reusing the operation ID on retry', async () => {
    await uploaded();
    http.expectNone(`${BASE}/payslip-imports/d-1/approve`); // nothing applied on upload

    const first = store.approve();
    const failed = http.expectOne(`${BASE}/payslip-imports/d-1/approve`);
    const body = failed.request.body;
    expect(body).toMatchObject({expectedDraftVersion: 1, expectedProfileVersion: 2, fields: ['payroll.baseGross']});
    failed.flush({details: {payslipCode: 'BUSINESS_UNAVAILABLE'}}, {status: 503, statusText: 'x'});
    await first;
    expect(store.phase()).toBe('review');
    expect(store.error()?.retryable).toBe(true);

    const second = store.approve();
    const retried = http.expectOne(`${BASE}/payslip-imports/d-1/approve`);
    expect(retried.request.body.operationId).toBe(body.operationId);
    retried.flush({acceptedFields: ['payroll.baseGross'], newCaseVersion: 5, newProfileVersion: 3, conflicts: [], missingInformation: []});
    await second;
    expect(store.phase()).toBe('done');
    expect(store.result()?.newProfileVersion).toBe(3);
  });

  it('flags a stale profile and blocks approval until a new version arrives', async () => {
    await uploaded();
    const pending = store.approve();
    http
      .expectOne(`${BASE}/payslip-imports/d-1/approve`)
      .flush({details: {payslipCode: 'PROFILE_VERSION_CONFLICT'}}, {status: 409, statusText: 'x'});
    await pending;
    expect(store.profileStale()).toBe(true);
    expect(store.phase()).toBe('review');
    store.configure('c-1', 3);
    expect(store.profileStale()).toBe(false);
    expect(store.canApprove()).toBe(true);
  });

  it('reloads the draft after a draft version conflict', async () => {
    await uploaded();
    const pending = store.approve();
    http
      .expectOne(`${BASE}/payslip-imports/d-1/approve`)
      .flush({details: {payslipCode: 'DRAFT_VERSION_CONFLICT'}}, {status: 409, statusText: 'x'});
    await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve));
    http.expectOne(`${BASE}/payslip-imports/d-1`).flush(draft({version: 3}));
    await pending;
    expect(store.draft()?.version).toBe(3);
    expect(store.error()?.code).toBe('DRAFT_VERSION_CONFLICT');
  });

  it('returns to upload when the draft is gone', async () => {
    await uploaded();
    const pending = store.approve();
    http
      .expectOne(`${BASE}/payslip-imports/d-1/approve`)
      .flush({details: {payslipCode: 'DRAFT_NOT_FOUND'}}, {status: 404, statusText: 'x'});
    await pending;
    expect(store.phase()).toBe('idle');
    expect(store.draft()).toBeNull();
    expect(store.error()?.message).toContain('להעלות');
  });

  it('cancels a pending draft on the server', async () => {
    await uploaded();
    const pending = store.cancel();
    http.expectOne({method: 'DELETE', url: `${BASE}/payslip-imports/d-1`}).flush(null, {status: 204, statusText: 'x'});
    await pending;
    expect(store.phase()).toBe('idle');
  });

  it('cannot approve without a known profile version', async () => {
    store.configure('c-1', null);
    await uploaded();
    expect(store.canApprove()).toBe(false);
  });
});
