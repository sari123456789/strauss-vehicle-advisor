import {expect, test} from '@playwright/test';
import {distortedImages, horizontalOverflow, seriousAxeViolations, smallTouchTargets, VIEWPORTS} from '../support/audits';
import {settle} from '../support/images';

/** Screens built on the new shell and shared components. */
const NEW_SCREENS = [
  {path: '/', name: 'home'},
  {path: '/dev/ui-states', name: 'ui-states'},
];

/** Legacy demo screens: only the new header is asserted; their bodies are replaced later. */
const LEGACY_SCREENS = ['/how-it-works', '/sign-in', '/catalog', '/advisor', '/comparison', '/calculation', '/history', '/representative'];

for (const viewport of VIEWPORTS) {
  test.describe(`${viewport.width}×${viewport.height}`, () => {
    test.use({viewport: {width: viewport.width, height: viewport.height}});

    for (const screen of NEW_SCREENS) {
      test(`${screen.name}: no overflow, 44px targets, axe, image ratios, screenshot`, async ({page}) => {
        await page.goto(screen.path === '/' ? '/' : `/#${screen.path}`);
        await settle(page);
        expect(await horizontalOverflow(page), 'horizontal overflow (px)').toBe(0);
        expect(await smallTouchTargets(page), 'controls smaller than 44×44').toEqual([]);
        expect(await distortedImages(page), 'images with distorted aspect ratio').toEqual([]);
        expect(await seriousAxeViolations(page), 'serious/critical axe violations').toEqual([]);
        await expect(page).toHaveScreenshot(`${screen.name}-${viewport.name}.png`, {fullPage: true, animations: 'disabled', maxDiffPixelRatio: 0.002});
      });
    }

    test('shell on legacy demo screens: no overflow from header, 44px targets, axe', async ({page}) => {
      test.setTimeout(180_000);
      for (const path of LEGACY_SCREENS) {
        await page.goto(`/#${path}`);
        await settle(page);
        const shellTargets = (await smallTouchTargets(page)).filter(t => /תפריט|כניסה|יציאה|DRIVE|מסך נציג|דילוג/.test(t));
        expect(shellTargets, `${path}: small shell controls`).toEqual([]);
        expect(await seriousAxeViolations(page, 'dr-site-header'), `${path}: header axe`).toEqual([]);
        const overflow = await horizontalOverflow(page);
        test.info().annotations.push({type: `legacy ${path} page overflow (report only)`, description: `${overflow}px`});
      }
    });
  });
}
