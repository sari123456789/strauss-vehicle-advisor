import AxeBuilder from '@axe-core/playwright';
import {Page} from '@playwright/test';

export const VIEWPORTS = [
  {name: '360', width: 360, height: 740},
  {name: '390', width: 390, height: 844},
  {name: '768', width: 768, height: 1024},
  {name: '1280', width: 1280, height: 800},
  {name: '1586', width: 1586, height: 992},
  {name: '1920', width: 1920, height: 1080},
] as const;

/** Horizontal page overflow in CSS px (0 = none). */
export async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
}

/**
 * Visible interactive controls smaller than `min`×`min` CSS px.
 * The only exemption is an inline link inside running text (WCAG 2.5.8 inline exception).
 */
export async function smallTouchTargets(page: Page, min = 44): Promise<string[]> {
  return page.evaluate(min => {
    const selector = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], [tabindex]:not([tabindex="-1"])';
    const out: string[] = [];
    for (const el of Array.from(document.querySelectorAll<HTMLElement>(selector))) {
      const style = getComputedStyle(el);
      if (style.visibility === 'hidden' || style.display === 'none' || el.closest('[inert], dialog:not([open])')) continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0 || r.bottom <= 0 || r.right <= 0 || r.top >= innerHeight * 50) continue;
      const inlineTextLink = el.tagName === 'A' && style.display === 'inline' && !!el.closest('p, li') && el.parentElement?.textContent?.trim() !== el.textContent?.trim();
      if (inlineTextLink) continue;
      if (r.width < min - 0.5 || r.height < min - 0.5) {
        const label = (el.getAttribute('aria-label') || el.textContent || el.tagName).trim().replace(/\s+/g, ' ').slice(0, 40);
        out.push(`${el.tagName.toLowerCase()} "${label}" ${Math.round(r.width)}×${Math.round(r.height)}`);
      }
    }
    return out;
  }, min);
}

/** Visible images whose rendered box ratio differs from the source ratio without object-fit cover/contain. */
export async function distortedImages(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    for (const img of Array.from(document.images)) {
      const r = img.getBoundingClientRect();
      if (!img.naturalWidth || r.width === 0 || r.height === 0) continue;
      const natural = img.naturalWidth / img.naturalHeight;
      const rendered = r.width / r.height;
      const fit = getComputedStyle(img).objectFit;
      if (Math.abs(rendered / natural - 1) > 0.01 && fit !== 'cover' && fit !== 'contain') {
        out.push(`${img.currentSrc.split('/').pop()} natural ${natural.toFixed(3)} rendered ${rendered.toFixed(3)} (${fit})`);
      }
    }
    return out;
  });
}

/** axe-core violations with serious or critical impact. */
export async function seriousAxeViolations(page: Page, include?: string): Promise<string[]> {
  let builder = new AxeBuilder({page}).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']);
  if (include) builder = builder.include(include);
  const results = await builder.analyze();
  return results.violations
    .filter(v => v.impact === 'serious' || v.impact === 'critical')
    .map(v => `${v.id} (${v.impact}): ${v.nodes.slice(0, 3).map(n => n.target.join(' ')).join(' | ')}`);
}
