import {expect, Page, test} from '@playwright/test';
import {settle} from '../support/images';

/** Resolves once the sheet's entrance animation has finished, so geometry is final. */
async function animationsDone(page: Page): Promise<void> {
  await page.locator('dialog[open] .sheet__panel').evaluate(el => Promise.all(el.getAnimations().map(a => a.finished)));
}

async function activeInsideOpenDialog(page: Page): Promise<boolean> {
  return page.evaluate(() => !!document.activeElement?.closest('dialog[open]'));
}

test.describe('navigation drawer (mobile and tablet)', () => {
  for (const viewport of [{width: 360, height: 740}, {width: 390, height: 844}, {width: 768, height: 1024}]) {
    test(`${viewport.width}px: opens modal, traps focus, Esc closes, focus returns`, async ({page}) => {
      await page.setViewportSize(viewport);
      await page.goto('/');
      await settle(page);
      const menuButton = page.getByRole('button', {name: 'תפריט ראשי'});
      await expect(menuButton).toBeVisible();
      await expect(page.locator('.site-nav')).toBeHidden();

      await menuButton.click();
      const dialog = page.getByRole('dialog', {name: 'תפריט'});
      await expect(dialog).toBeVisible();
      expect(await dialog.evaluate(d => d.matches(':modal'))).toBe(true);
      expect(await page.evaluate(() => document.documentElement.classList.contains('scroll-locked'))).toBe(true);
      expect(await activeInsideOpenDialog(page)).toBe(true);

      for (const key of [...Array(12).fill('Tab'), ...Array(12).fill('Shift+Tab')]) {
        await page.keyboard.press(key);
        expect(await activeInsideOpenDialog(page), `focus left the drawer on ${key}`).toBe(true);
      }

      // Drawer opens from the inline-start (right) edge and stays inside the viewport.
      await animationsDone(page);
      const box = (await dialog.locator('.sheet__panel').boundingBox())!;
      expect(Math.round(box.x + box.width)).toBe(viewport.width);
      expect(box.x).toBeGreaterThanOrEqual(0);

      await page.keyboard.press('Escape');
      await expect(dialog).toBeHidden();
      await expect(menuButton).toBeFocused();
      expect(await page.evaluate(() => document.documentElement.classList.contains('scroll-locked'))).toBe(false);
    });
  }

  test('navigating from the drawer closes it', async ({page}) => {
    await page.setViewportSize({width: 390, height: 844});
    await page.goto('/');
    await page.getByRole('button', {name: 'תפריט ראשי'}).click();
    await page.getByRole('dialog', {name: 'תפריט'}).getByRole('link', {name: 'איך זה עובד'}).click();
    await expect(page).toHaveURL(/#\/how-it-works$/);
    await expect(page.getByRole('dialog', {name: 'תפריט'})).toBeHidden();
  });

  test('desktop shows inline navigation and no menu button', async ({page}) => {
    await page.setViewportSize({width: 1280, height: 800});
    await page.goto('/');
    await expect(page.getByRole('button', {name: 'תפריט ראשי'})).toBeHidden();
    await expect(page.locator('.site-nav').getByRole('link', {name: 'השוואת רכבים'})).toBeVisible();
  });
});

test.describe('sheets with long Hebrew content', () => {
  test('390px: bottom sheet sits on the bottom edge, wraps long text, closes by backdrop', async ({page}) => {
    await page.setViewportSize({width: 390, height: 844});
    await page.goto('/#/dev/ui-states');
    await settle(page);
    const trigger = page.getByRole('button', {name: 'היסטוריית שיחות'});
    await trigger.click();
    const dialog = page.getByRole('dialog', {name: /היסטוריית שיחות/});
    await expect(dialog).toBeVisible();
    const panel = dialog.locator('.sheet__panel');
    await animationsDone(page);
    const box = (await panel.boundingBox())!;
    expect(Math.round(box.y + box.height)).toBe(844);
    expect(box.height).toBeLessThanOrEqual(844 * 0.9 + 1);
    const body = dialog.locator('.sheet__body');
    expect(await body.evaluate(el => el.scrollWidth - el.clientWidth), 'horizontal overflow inside sheet').toBe(0);
    expect(await body.evaluate(el => el.scrollHeight > el.clientHeight), 'long content scrolls inside the sheet').toBe(true);

    await page.mouse.click(195, 20); // backdrop above the sheet
    await expect(dialog).toBeHidden();
    await expect(trigger).toBeFocused();
  });

  test('768px: auto sheet becomes a side drawer', async ({page}) => {
    await page.setViewportSize({width: 768, height: 1024});
    await page.goto('/#/dev/ui-states');
    await page.getByRole('button', {name: 'פרטי התיק'}).click();
    const panel = page.getByRole('dialog', {name: 'פרטי התיק'}).locator('.sheet__panel');
    await animationsDone(page);
    const box = (await panel.boundingBox())!;
    expect(Math.round(box.x + box.width)).toBe(768);
    expect(Math.round(box.height)).toBe(1024);
    expect(box.width).toBeLessThanOrEqual(420);
  });

  test('error retry works and states expose roles', async ({page}) => {
    await page.setViewportSize({width: 360, height: 740});
    await page.goto('/#/dev/ui-states');
    await expect(page.getByRole('alert').first()).toContainText('יש תקלה זמנית');
    await expect(page.getByRole('status').first()).toHaveAttribute('aria-busy', 'true');
    await page.getByRole('button', {name: 'לנסות שוב'}).click();
    await expect(page.getByText('ניסיונות חוזרים: 1')).toBeVisible();
    await expect(page.locator('body')).not.toContainText(/\b(404|409|500|503)\b/);
  });
});
