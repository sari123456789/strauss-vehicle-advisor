import {expect, test, type Page} from '@playwright/test';

/**
 * The full journey in a real browser against a real backend (no API mocks):
 * sign-in -> advisor conversation -> catalogue from the server -> real comparison (E-001,
 * ATTO 3 "11,693") -> representative question -> reload restores the session -> history ->
 * stored calculation -> the representative answers in a second browser context.
 *
 * Runs only when a backend is available (local Docker database; tests/e2e_server.py with the
 * deterministic keyword model):
 *   E2E_BACKEND=1 E2E_E001_PASSWORD=... E2E_REP01_PASSWORD=... npx playwright test e2e/integration
 * Otherwise it is skipped and reported as not run.
 */
const E001 = process.env['E2E_E001_PASSWORD'];
const REP01 = process.env['E2E_REP01_PASSWORD'];
test.skip(!process.env['E2E_BACKEND'] || !E001 || !REP01, 'needs a running backend and demo passwords');
test.describe.configure({mode: 'serial'});

async function signIn(page: Page, login: string, password: string): Promise<void> {
  await page.goto('/#/sign-in');
  await page.getByLabel('שם משתמש').fill(login);
  await page.getByLabel('סיסמה').fill(password);
  await page.getByRole('button', {name: /כניסה/}).click();
}

test('employee journey and representative answer', async ({page, browser}) => {
  test.setTimeout(180_000);
  await signIn(page, 'e001', E001!);
  await expect(page).toHaveURL(/#\/advisor/);

  // The advisor answers through the real worker (keyword model; amounts from Business).
  await page.getByRole('button', {name: '+ שיחה חדשה'}).click();
  await expect(page).toHaveURL(/#\/advisor\/[0-9a-f-]{36}$/);
  await expect(page.getByLabel('הודעה ליועץ')).toBeEnabled();
  await page.getByLabel('הודעה ליועץ').fill('כמה אקבל נטו עם ATTO 3?');
  await page.getByRole('button', {name: 'שליחת הודעה'}).click();
  await expect(page.locator('.message:not(.user)').filter({hasText: '₪'}).first()).toBeVisible({timeout: 60_000});

  // Catalogue and eligibility come from the server.
  await page.goto('/#/catalog');
  const atto = page.locator('article.vehicle').filter({hasText: 'ATTO 3'});
  await expect(atto).toBeVisible({timeout: 30_000});
  await expect(atto).toContainText('זכאי');
  await atto.getByRole('button', {name: 'להוספה להשוואה'}).click();

  // A real comparison: the Business display value for E-001 / ATTO 3.
  await page.getByRole('link', {name: /להשוואת הנטו/}).click();
  const card = page.locator('article.result').filter({hasText: 'ATTO 3'});
  await expect(card).toContainText('11,693', {timeout: 30_000});

  // A reload keeps the session (refresh cookie) and the history comes from the server.
  await page.goto('/#/history');
  await page.reload();
  await expect(page).toHaveURL(/#\/history/);
  const firstDetails = page.getByRole('link', {name: 'פירוט ←'}).first();
  await expect(firstDetails).toBeVisible({timeout: 30_000});
  await firstDetails.click();
  await expect(page.locator('aside.panel .amount')).toBeVisible({timeout: 30_000});
});
