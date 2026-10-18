// Fails when development-only code or fixtures appear in the production bundle (dist/).
// Run after `ng build` (production configuration).
import fs from 'node:fs';
import path from 'node:path';

const dist = path.resolve(import.meta.dirname, '..', 'dist');
const FORBIDDEN = [
  'dev/ui-states',           // development-only route (src/app/dev/dev-routes.development.ts)
  'example.invalid',         // development-only sample text
  'רכיבי מצב משותפים',       // development-only page title
  'mock.local',              // dev mock server accounts (future)
  '__mock',                  // dev mock server control routes (future)
];

if (!fs.existsSync(dist)) {
  console.error(`No build output at ${dist}; run "ng build" first.`);
  process.exit(2);
}

const hits = [];
for (const entry of fs.readdirSync(dist, {recursive: true, withFileTypes: true})) {
  if (!entry.isFile() || !/\.(js|html|css|json|txt)$/.test(entry.name)) continue;
  const file = path.join(entry.parentPath, entry.name);
  const content = fs.readFileSync(file, 'utf8');
  for (const marker of FORBIDDEN) {
    if (content.includes(marker)) hits.push(`${path.relative(dist, file)}: ${marker}`);
  }
}

if (hits.length) {
  console.error('Development-only content found in the production bundle:\n  ' + hits.join('\n  '));
  process.exit(1);
}
console.log('Production bundle check passed: no development-only content in dist/.');
