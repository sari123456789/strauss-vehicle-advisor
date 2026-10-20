import {provideHttpClient} from '@angular/common/http';
import {HttpTestingController, provideHttpClientTesting} from '@angular/common/http/testing';
import {TestBed} from '@angular/core/testing';
import {PayslipImportComponent} from './payslip-import.component';
import {draft} from './payslip.testing';
import {PayslipApprovalResult} from './payslip.models';

describe('PayslipImportComponent', () => {
  async function setup() {
    TestBed.configureTestingModule({
      imports: [PayslipImportComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    const fixture = TestBed.createComponent(PayslipImportComponent);
    fixture.componentRef.setInput('conversationId', 'c-1');
    fixture.componentRef.setInput('profileVersion', 2);
    await fixture.whenStable();
    const http = TestBed.inject(HttpTestingController);
    const element = fixture.nativeElement as HTMLElement;
    return {fixture, http, element};
  }

  it('renders the review with evidence, statuses and alternatives, then emits applied', async () => {
    const {fixture, http, element} = await setup();
    const applied: PayslipApprovalResult[] = [];
    fixture.componentInstance.applied.subscribe((r) => applied.push(r));

    const input = element.querySelector<HTMLInputElement>('[data-testid="payslip-file"]');
    expect(input).not.toBeNull();
    const pending = fixture.componentInstance.store.upload(new File([new Uint8Array(10)], 'p.pdf', {type: 'application/pdf'}));
    http.expectOne('/api/v1/conversations/c-1/payslip-imports').flush(draft(), {status: 201, statusText: 'Created'});
    await pending;
    await fixture.whenStable();

    const text = element.textContent ?? '';
    expect(text).toContain('שכר בסיס חודשי');
    expect(text).toContain('18,500.00 ₪');
    expect(text).toContain('מקור: עמוד 1');
    expect(text).toContain('נמצאו כמה ערכים');
    expect(text).toContain('6%');
    const bonus = element.querySelector('[data-field="payroll.bonusGross"] input[type="checkbox"]') as HTMLInputElement;
    expect(bonus.disabled).toBe(true);

    const approve = element.querySelector<HTMLButtonElement>('[data-testid="payslip-approve"]')!;
    expect(approve.disabled).toBe(false);
    approve.click();
    http
      .expectOne('/api/v1/payslip-imports/d-1/approve')
      .flush({acceptedFields: ['payroll.baseGross'], newCaseVersion: 5, newProfileVersion: 3, conflicts: [], missingInformation: []});
    await new Promise((resolve) => setTimeout(resolve));
    await fixture.whenStable();
    expect(applied.map((r) => r.newProfileVersion)).toEqual([3]);
    expect(element.textContent).toContain('הפרופיל עודכן');
    http.verify();
  });

  it('shows a Hebrew alert for an invalid file and makes no request', async () => {
    const {fixture, http, element} = await setup();
    await fixture.componentInstance.store.upload(new File(['x'], 'a.txt', {type: 'text/plain'}));
    await fixture.whenStable();
    expect(element.querySelector('[role="alert"]')?.textContent).toContain('PDF');
    http.verify();
  });
});
