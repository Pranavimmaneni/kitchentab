// Render HTML receipts to text PDFs and base PNG screenshots with Playwright's Chromium.
// Usage: node scripts/receipts/render.mjs <jobs.json>
// jobs: [{ "html": abs path, "pdf": abs path?, "png": abs path?, "width": css px, "scale": dpr,
//          "pdfFormat": "Letter"|null, "pageHeight": css px|null, "footer": bool }]
// PLAYWRIGHT_BROWSERS_PATH must point at <repo>/.cache/ms-playwright (set by generate.py).
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '..', '..');
// Reuse the Playwright the web app already depends on (no new package.json entry).
const require = createRequire(path.join(repo, 'apps', 'web', 'package.json'));
const { chromium } = require('@playwright/test');

const jobs = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const browser = await chromium.launch();
try {
  for (const job of jobs) {
    const context = await browser.newContext({
      viewport: { width: job.width, height: 800 },
      deviceScaleFactor: job.scale ?? 1,
    });
    const page = await context.newPage();
    await page.goto(pathToFileURL(job.html).href, { waitUntil: 'load' });
    if (job.pdf) {
      if (job.pdfFormat) {
        await page.pdf({
          path: job.pdf,
          format: job.pdfFormat,
          margin: { top: '0.5in', bottom: '0.6in', left: '0.5in', right: '0.5in' },
          printBackground: true,
          displayHeaderFooter: !!job.footer,
          headerTemplate: '<span></span>',
          footerTemplate:
            '<div style="font-size:9px;font-family:Arial;width:100%;text-align:center;color:#555">' +
            'Page <span class="pageNumber"></span> of <span class="totalPages"></span></div>',
        });
      } else {
        const height = job.pageHeight ?? (await page.evaluate(() => document.documentElement.scrollHeight + 8));
        await page.pdf({
          path: job.pdf,
          width: `${job.width}px`,
          height: `${height}px`,
          margin: { top: '0px', bottom: '0px', left: '0px', right: '0px' },
          printBackground: true,
        });
      }
    }
    if (job.png) {
      await page.screenshot({ path: job.png, fullPage: true });
    }
    await context.close();
    process.stdout.write(`rendered ${path.basename(job.html)}\n`);
  }
} finally {
  await browser.close();
}
