import fs from 'node:fs';
import path from 'node:path';
import {Page} from '@playwright/test';
import pixelmatch from 'pixelmatch';
import {PNG} from 'pngjs';

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export function readPng(file: string): PNG {
  return PNG.sync.read(fs.readFileSync(file));
}

export function writePng(file: string, png: PNG): void {
  fs.mkdirSync(path.dirname(file), {recursive: true});
  fs.writeFileSync(file, PNG.sync.write(png));
}

/** Paints a box with a flat color (used to mask intentional differences identically on both images). */
export function fillBox(png: PNG, box: Box, rgb: [number, number, number]): void {
  for (let y = box.y; y < box.y + box.h; y++) {
    for (let x = box.x; x < box.x + box.w; x++) {
      const i = (y * png.width + x) * 4;
      png.data[i] = rgb[0];
      png.data[i + 1] = rgb[1];
      png.data[i + 2] = rgb[2];
      png.data[i + 3] = 255;
    }
  }
}

export function diffImages(a: PNG, b: PNG, threshold = 0.1): {mismatched: number; ratio: number; diff: PNG} {
  const diff = new PNG({width: a.width, height: a.height});
  const mismatched = pixelmatch(a.data, b.data, diff.data, a.width, a.height, {threshold});
  return {mismatched, ratio: mismatched / (a.width * a.height), diff};
}

/** Places images side by side (left: a, right: b) for manual review. */
export function sideBySide(a: PNG, b: PNG): PNG {
  const out = new PNG({width: a.width + b.width, height: Math.max(a.height, b.height)});
  PNG.bitblt(a, out, 0, 0, a.width, a.height, 0, 0);
  PNG.bitblt(b, out, 0, 0, b.width, b.height, a.width, 0);
  return out;
}

/** Mean color of the pixels inside `box` that satisfy `predicate` (all pixels by default). */
export function meanColor(png: PNG, box: Box, predicate: (r: number, g: number, b: number) => boolean = () => true): [number, number, number] | null {
  let r = 0, g = 0, b = 0, n = 0;
  for (let y = box.y; y < box.y + box.h; y++) {
    for (let x = box.x; x < box.x + box.w; x++) {
      const i = (y * png.width + x) * 4;
      if (predicate(png.data[i], png.data[i + 1], png.data[i + 2])) {
        r += png.data[i]; g += png.data[i + 1]; b += png.data[i + 2]; n++;
      }
    }
  }
  return n ? [r / n, g / n, b / n] : null;
}

function toLab([r, g, b]: [number, number, number]): [number, number, number] {
  const lin = (c: number) => {
    c /= 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  const [R, G, B] = [lin(r), lin(g), lin(b)];
  const X = (R * 0.4124 + G * 0.3576 + B * 0.1805) / 0.95047;
  const Y = R * 0.2126 + G * 0.7152 + B * 0.0722;
  const Z = (R * 0.0193 + G * 0.1192 + B * 0.9505) / 1.08883;
  const f = (t: number) => (t > 216 / 24389 ? Math.cbrt(t) : (24389 / 27 * t + 16) / 116);
  return [116 * f(Y) - 16, 500 * (f(X) - f(Y)), 200 * (f(Y) - f(Z))];
}

/** CIE76 colour difference. */
export function deltaE(a: [number, number, number], b: [number, number, number]): number {
  const [l1, a1, b1] = toLab(a);
  const [l2, a2, b2] = toLab(b);
  return Math.hypot(l1 - l2, a1 - a2, b1 - b2);
}

/** Waits until web fonts and every eager <img> are loaded, decoded and painted. */
export async function settle(page: Page): Promise<void> {
  await page.waitForLoadState('networkidle');
  await page.evaluate(async () => {
    await document.fonts.ready;
    // Only rendered images: a lazy image inside display:none never loads, so its decode() never settles.
    const images = Array.from(document.images).filter(img => {
      const r = img.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && (img.loading !== 'lazy' || r.top < innerHeight);
    });
    await Promise.all(images.map(img => img.decode().catch(() => undefined)));
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  });
}
