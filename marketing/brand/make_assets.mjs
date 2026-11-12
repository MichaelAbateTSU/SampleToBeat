// Brand assets from the live app and the logo SVG: app icons, the social sharing card, press screenshots.
//   server running on :8765, then:  node brand/make_assets.mjs
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const { chromium } = createRequire(import.meta.url)("playwright");
const here = dirname(fileURLToPath(import.meta.url));
const pub = resolve(here, "../../frontend/public");
const BASE = process.env.BASE_URL ?? "http://localhost:8765";
const svg = readFileSync(`${pub}/favicon.svg`, "utf8");

const browser = await chromium.launch();
const page = await browser.newPage();
for (const size of [180, 192, 512, 1024]) {
  await page.setViewportSize({ width: size, height: size });
  await page.setContent(`<style>*{margin:0}svg{display:block;width:${size}px;height:${size}px}</style>${svg}`);
  await page.screenshot({ path: size === 1024 ? `${here}/logo-mark-1024.png` : `${pub}/icon-${size}.png`, omitBackground: true });
}

// social card (1200x630): the real landing hero
const card = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1 });
await card.goto(BASE);
await card.locator(".machine").waitFor();
await card.addStyleTag({ content: ".nav{position:static} .hero-inner{padding-top:34px;gap:36px;grid-template-columns:minmax(0,1fr) 470px} .hero-points{display:none} .machine .dropzone{display:none}" });
await card.screenshot({ path: `${pub}/og.png`, clip: { x: 0, y: 0, width: 1200, height: 630 } });

// press screenshots at 2x
const shot = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
await shot.goto(BASE);
await shot.locator(".machine").waitFor();
await shot.screenshot({ path: `${here}/screenshot-landing.png` });
await browser.close();
console.log("assets written");
