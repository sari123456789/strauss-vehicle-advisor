import {APIRequestContext, expect, request as playwrightRequest, test} from '@playwright/test';
import path from 'node:path';

/**
 * REAL end-to-end payslip journey: Angular (ng serve + proxy) -> FastAPI -> Business -> local
 * PostgreSQL. Opt-in, because it needs the running backend on 127.0.0.1:8000 and seeded
 * local accounts:
 *
 *   E2E_PAYSLIP=1 E2E_LOGIN=e001 E2E_OTHER_LOGIN=e002 E2E_PASSWORD=... npx playwright test e2e/payslip --project=chromium
 *
 * Uses the synthetic fixture only; the backend runs with STRAUSS_PAYSLIP_EXTERNAL_LLM=false.
 */
const enabled = process.env['E2E_PAYSLIP'] === '1';
const login = process.env['E2E_LOGIN'] ?? 'e001';
const otherLogin = process.env['E2E_OTHER_LOGIN'] ?? 'e002';
const password = process.env['E2E_PASSWORD'] ?? '';
const API = 'http://127.0.0.1:8000/api/v1';
const FIXTURE = path.resolve(__dirname, '../../../tests/fakes/payslip/fixtures/ambiguous.pdf');

async function apiAs(user: string): Promise<{api: APIRequestContext; auth: Record<string, string>}> {
  const api = await playwrightRequest.newContext();
  const response = await api.post(`${API}/auth/login`, {data: {login: user, password}});
  expect(response.ok()).toBe(true);
  return {api, auth: {Authorization: `Bearer ${(await response.json()).accessToken}`}};
}

interface StoredProfile {
  profileVersion: number;
  baseGross: {value: string | null};
  pensionEmployeeRate: {value: string | null};
}

async function profile(user: string): Promise<StoredProfile> {
  const {api, auth} = await apiAs(user);
  const body = await (await api.get(`${API}/me/profile`, {headers: auth})).json();
  await api.dispose();
  return body.data as StoredProfile;
}

async function profileVersion(user: string): Promise<number> {
  return (await profile(user)).profileVersion;
}

test.describe('payslip import (real stack)', () => {
  test.skip(!enabled || !password, 'set E2E_PAYSLIP=1 and E2E_PASSWORD with the local backend running');

  test('upload, review, correct, approve once, profile refreshed, owner-only draft', async ({page}) => {
    test.setTimeout(120_000);
    // Pick values that differ from the stored ones, so every run is a real (non no-op) update.
    const before = await profile(login);
    const versionBefore = before.profileVersion;
    const [baseTyped, baseShown] = before.baseGross.value === '15000.00' ? ['16000', '16,000.00 ₪'] : ['15000', '15,000.00 ₪'];
    const [pensionTyped, pensionShown] = before.pensionEmployeeRate.value === '0.06' ? ['7', '0.07'] : ['6', '0.06'];

    // 1-2. Real login (employees land on the advisor), whose side panel shows the real salary facts.
    await page.goto('/#/sign-in');
    await page.getByTestId('login').fill(login);
    await page.getByTestId('password').fill(password);
    await page.getByTestId('sign-in').click();
    await expect(page).toHaveURL(/#\/advisor/);
    const section = page.getByTestId('real-profile');
    await expect(section.locator('[data-field="payroll.baseGross"]')).toBeVisible();

    // 3-4. Upload the synthetic Hebrew PDF; the backend extracts a reviewable draft.
    await page.getByTestId('chat-open-payslip-import').click();
    await page.getByTestId('payslip-file').setInputFiles(FIXTURE);
    const base = page.locator('li.payslip-field[data-field="payroll.baseGross"]');
    await expect(base).toHaveAttribute('data-status', 'needs_review');
    await expect(base).toContainText('מקור: עמוד 1');
    expect(await profileVersion(login)).toBe(versionBefore); // nothing applied yet

    // 5. Correct the base salary (its line's hourly rate and hours are not offered) and type the
    // pension rate as a percent.
    await base.getByRole('button', {name: /תיקון|הזנת ערך/}).click();
    await base.locator('input[inputmode="decimal"]').fill(baseTyped);
    await base.getByRole('button', {name: 'שמירה'}).click();
    await expect(base).toHaveAttribute('data-status', 'corrected');
    const pension = page.locator('li.payslip-field[data-field="payroll.pensionEmployeeRate"]');
    await pension.getByRole('button', {name: /תיקון|הזנת ערך/}).click();
    await pension.locator('input[inputmode="decimal"]').fill(pensionTyped);
    await pension.getByRole('button', {name: 'שמירה'}).click();
    await expect(pension).toHaveAttribute('data-status', 'corrected');

    // 6-7. Explicit approval; capture the exact request to replay it later.
    const approveRequest = page.waitForRequest((r) => r.url().endsWith('/approve') && r.method() === 'POST');
    await page.getByTestId('payslip-approve').click();
    const sent = await approveRequest;
    const approveUrl = sent.url();
    const approveBody = sent.postDataJSON();
    await expect(page.getByText('הפרופיל עודכן.')).toBeVisible();
    expect(approveBody.fields.sort()).toEqual(['payroll.baseGross', 'payroll.pensionEmployeeRate']);

    // 8. The profile shown is reloaded from the server.
    await expect(section.locator('[data-field="payroll.baseGross"] .value')).toContainText(baseShown);
    await expect(section.locator('[data-field="payroll.pensionEmployeeRate"] .value')).toHaveText(pensionShown);
    expect(await profileVersion(login)).toBe(versionBefore + 1);

    // 9. A second identical approval replays the result and changes nothing.
    const owner = await apiAs(login);
    const draftUrl = `${API}${new URL(approveUrl).pathname.replace(/^\/api\/v1/, '').replace(/\/approve$/, '')}`;
    const replay = await owner.api.post(`${draftUrl}/approve`, {headers: owner.auth, data: approveBody});
    expect(replay.status()).toBe(200);
    expect((await replay.json()).newProfileVersion).toBe(versionBefore + 1);
    expect(await profileVersion(login)).toBe(versionBefore + 1);
    await owner.api.dispose();

    // 10. Another employee cannot see or approve the draft.
    const other = await apiAs(otherLogin);
    expect((await other.api.get(draftUrl, {headers: other.auth})).status()).toBe(404);
    const foreign = await other.api.post(`${draftUrl}/approve`, {headers: other.auth, data: approveBody});
    expect(foreign.status()).toBe(404);
    await other.api.dispose();
  });
});
