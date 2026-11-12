// Renders the trailer's captions and end card as transparent 1920x1080 PNGs, set in the app's own typefaces.
//   node captions.mjs <out_dir>
import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const { chromium } = createRequire(import.meta.url)("playwright");
const OUT = process.argv[2];
mkdirSync(OUT, { recursive: true });
const fonts = resolve(dirname(fileURLToPath(import.meta.url)), "../../frontend/node_modules/@fontsource-variable");
// inlined: a page made with setContent may not load file:// fonts
const font = (pkg, file) => `data:font/woff2;base64,${readFileSync(`${fonts}/${pkg}/files/${file}`).toString("base64")}`;

const CSS = `
@font-face { font-family: Archivo; src: url("${font("archivo", "archivo-latin-standard-normal.woff2")}"); font-weight: 100 900; font-stretch: 62% 125%; }
* { margin: 0; box-sizing: border-box; }
html, body { width: 1920px; height: 1080px; background: transparent; font-family: Archivo, sans-serif; overflow: hidden; }
.film { position: absolute; left: 110px; bottom: 110px; color: #f6f1e8; font-weight: 900; font-stretch: 125%; font-size: 104px; line-height: 0.98; text-shadow: 0 4px 40px rgba(0,0,0,.75), 0 2px 6px rgba(0,0,0,.6); }
.key { position: absolute; left: 50%; bottom: 64px; transform: translateX(-50%); white-space: nowrap; background: #17181a; color: #f3f3f0; font-weight: 850; font-stretch: 118%; font-size: 58px; line-height: 1; padding: 26px 44px 28px; border-radius: 18px; border-bottom: 8px solid #f04e14; box-shadow: 0 24px 60px rgba(0,0,0,.45); }
.end { position: absolute; left: 990px; top: 0; bottom: 60px; display: grid; justify-items: start; align-content: center; color: #f6f1e8; }
.end .word { font-weight: 900; font-stretch: 125%; font-size: 150px; line-height: 1; }
.end .tag { font-weight: 600; font-size: 50px; margin-top: 18px; color: #d8d2c6; }
.end .cta { margin-top: 44px; background: #f04e14; color: #17181a; font-weight: 850; font-stretch: 112%; font-size: 40px; padding: 18px 38px 20px; border-radius: 14px; border-bottom: 6px solid #8a2a06; }
`;
const CAPTIONS = {
  c1: `<div class="film">Every sample</div>`,
  c2: `<div class="film">has a beat in it.</div>`,
  c3: `<div class="film">Drop it in.</div>`,
  c4: `<div class="key">It finds the tempo, key and groove</div>`,
  c5: `<div class="key">Four finished flips</div>`,
  c6: `<div class="key">Mix it your way</div>`,
  c7: `<div class="key">WAV. Stems. MIDI.</div>`,
  c8: `<div class="film">Finish more beats.</div>`,
  end: `<div class="end"><div class="word">ChopLab</div><div class="tag">Drop a sample. Get a beat.</div><div class="cta">Free to try</div></div>`,
};

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
for (const [name, html] of Object.entries(CAPTIONS)) {
  await page.setContent(`<style>${CSS}</style>${html}`);
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: `${OUT}/${name}.png`, omitBackground: true });
}
await browser.close();
console.log("captions:", Object.keys(CAPTIONS).join(" "));
