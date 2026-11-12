// Records the real app for the trailer: drives a browser through the product and saves lossless-ish frame
// sequences (Chrome screencast, JPEG q95, 1920x1080) for each scene, with a visible cursor.
//   server:  cd backend && CHOPLAB_BILLING=mock uv run choplab
//   run:     node capture.mjs <out_dir> <sample.wav>
import { mkdirSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";

const { chromium } = createRequire(import.meta.url)("playwright");
const [OUT, SAMPLE] = process.argv.slice(2);
const BASE = process.env.BASE_URL ?? "http://localhost:8765";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await chromium.launch();
// Chrome's screencast captures CSS pixels, so the page is laid out at 1280x720 and zoomed 1.5x into a
// 1920x1080 viewport: full-HD frames with the proportions of a laptop screen.
const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
await ctx.addInitScript(() => {
  // a cursor that headless Chrome will actually draw, plus a click ripple
  window.addEventListener("DOMContentLoaded", () => {
    document.documentElement.style.zoom = "1.5";
    const c = document.createElement("div");
    c.id = "fake-cursor";
    c.style.cssText = "position:fixed;z-index:99999;width:22px;height:22px;margin:-3px 0 0 -3px;pointer-events:none;left:-50px;top:-50px;transition:transform .12s;" +
      "background:url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'><path d='M4 2l15 11-6.5 1.2L16 21l-3 1.4-3.4-6.9L5 20z' fill='white' stroke='black' stroke-width='1.4' stroke-linejoin='round'/></svg>\") no-repeat;filter:drop-shadow(0 2px 4px rgba(0,0,0,.6))";
    document.body.appendChild(c);
    window.addEventListener("mousemove", (e) => { c.style.left = e.clientX + "px"; c.style.top = e.clientY + "px"; }, true);
    window.addEventListener("mousedown", () => { c.style.transform = "scale(.8)"; }, true);
    window.addEventListener("mouseup", () => { c.style.transform = ""; }, true);
  });
});
const page = await ctx.newPage();
const cdp = await ctx.newCDPSession(page);
let pos = { x: 960, y: 700 };

async function glide(locator, dx = 0, dy = 0) {
  const b = await locator.boundingBox();
  const to = { x: b.x + b.width / 2 + dx, y: b.y + b.height / 2 + dy };
  const steps = 28;
  for (let i = 1; i <= steps; i++) {
    const t = i / steps, e = t * t * (3 - 2 * t);
    await page.mouse.move(pos.x + (to.x - pos.x) * e, pos.y + (to.y - pos.y) * e);
    await sleep(14);
  }
  pos = to;
}
const click = async (locator, dx, dy) => { await glide(locator, dx, dy); await sleep(140); await page.mouse.down(); await sleep(70); await page.mouse.up(); };

async function record(name, fn) {
  const dir = `${OUT}/${name}`;
  mkdirSync(dir, { recursive: true });
  const frames = [];
  const onFrame = async (f) => {
    frames.push({ t: f.metadata.timestamp, data: f.data });
    await cdp.send("Page.screencastFrameAck", { sessionId: f.sessionId }).catch(() => {});
  };
  cdp.on("Page.screencastFrame", onFrame);
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 95, everyNthFrame: 1 });
  await page.mouse.move(pos.x + 1, pos.y);  // force a first frame
  const t0 = Date.now();
  await fn();
  const total = (Date.now() - t0) / 1000;
  await cdp.send("Page.stopScreencast");
  cdp.off("Page.screencastFrame", onFrame);
  let list = "ffconcat version 1.0\n";
  frames.forEach((f, i) => {
    const file = `f${String(i).padStart(5, "0")}.jpg`;
    writeFileSync(`${dir}/${file}`, Buffer.from(f.data, "base64"));
    const dur = i + 1 < frames.length ? frames[i + 1].t - f.t : Math.max(0.04, total - (f.t - frames[0].t));
    list += `file ${file}\nduration ${Math.max(0.001, dur).toFixed(4)}\n`;
  });
  if (frames.length) list += `file f${String(frames.length - 1).padStart(5, "0")}.jpg\n`;
  writeFileSync(`${dir}/list.txt`, list);
  console.log(`${name}: ${frames.length} frames, ${total.toFixed(1)}s`);
}

// an account on the Pro plan (mock billing), so the export scene can show WAV, stems and MIDI
await page.goto(`${BASE}/app/new`);
await page.evaluate(async () => {
  await fetch("/api/auth/register", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: `trailer-${Date.now()}@example.com`, password: "trailer password" }) });
  await fetch("/api/billing/mock-complete?plan=pro");
});
await page.goto(`${BASE}/app/new`);
await page.getByText("Drop your sample here").waitFor();

await record("create", async () => {
  await sleep(500);
  await glide(page.locator(".dropzone"));
  await page.locator('.dropzone input[type="file"]').setInputFiles(SAMPLE);
  await sleep(1100);
  await page.evaluate(() => document.querySelector(".genre-grid").scrollIntoView({ behavior: "smooth", block: "center" }));
  await sleep(700);
  await click(page.locator(".genre", { hasText: "Boom bap" }));
  await sleep(450);
  const energy = page.locator(".slider", { hasText: "Energy" }).locator("input");
  await glide(energy, 30, 0);
  await page.mouse.down();
  for (let i = 0; i < 16; i++) { await page.mouse.move(pos.x + i * 7, pos.y); await sleep(18); }
  pos.x += 105;
  await page.mouse.up();
  await sleep(350);
  await page.evaluate(() => window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" }));
  await sleep(650);
  await click(page.getByRole("button", { name: /Make my beat/ }));
  await page.locator(".progress-card").waitFor();
  await page.mouse.move(1700, 950, { steps: 12 });
  pos = { x: 1700, y: 950 };
  await sleep(4200);
});

await page.locator(".variation").first().waitFor({ timeout: 300_000 });
await page.locator(".timeline").waitFor();
await sleep(800);

await record("flips", async () => {
  await sleep(600);
  await click(page.locator(".variation .play-btn").nth(0));
  await sleep(2300);
  await glide(page.locator(".variation").nth(1));
  await sleep(500);
  await glide(page.locator(".variation").nth(3));
  await sleep(600);
  await page.evaluate(() => window.scrollTo({ top: document.querySelector(".studio-card").getBoundingClientRect().top + window.scrollY - 90, behavior: "smooth" }));
  await sleep(1300);
  await glide(page.locator(".tl-section").nth(1));
  await sleep(4500);
});

await record("mix", async () => {
  await sleep(300);
  await click(page.getByRole("button", { name: "Solo 808" }));
  await sleep(700);
  await click(page.getByRole("button", { name: "Solo Kick" }));
  await sleep(700);
  await click(page.getByRole("button", { name: "Solo 808" }));
  await click(page.getByRole("button", { name: "Solo Kick" }));
  await sleep(300);
  await click(page.getByRole("button", { name: "Mute Hi-hats" }));
  await sleep(500);
  await page.evaluate(() => document.querySelector(".studio-controls").scrollIntoView({ behavior: "smooth", block: "center" }));
  await sleep(800);
  const swing = page.locator(".slider", { hasText: "Swing" }).locator("input");
  await glide(swing, -20, 0);
  await page.mouse.down();
  for (let i = 0; i < 20; i++) { await page.mouse.move(pos.x + i * 6, pos.y); await sleep(20); }
  pos.x += 114;
  await page.mouse.up();
  await sleep(900);
});

await page.locator(".playerbar .play-btn").click();
await page.evaluate(() => window.scrollTo({ top: document.querySelector(".studio-card").getBoundingClientRect().top + window.scrollY - 90 }));
await page.getByRole("button", { name: "Undo changes" }).click();
await page.getByRole("tab", { name: /Export/ }).click();
await sleep(500);

await record("export", async () => {
  await sleep(400);
  await click(page.locator(".toggle-row", { hasText: "Track stems" }).locator(".toggle"));
  await sleep(350);
  await click(page.locator(".toggle-row", { hasText: "MIDI" }).locator(".toggle"));
  await sleep(350);
  await click(page.getByRole("button", { name: /Export beat/ }));
  await sleep(1200);
});
await page.locator(".files .file").nth(3).waitFor({ timeout: 180_000 });
await record("export_done", async () => {
  await sleep(500);
  await glide(page.locator(".files .file").nth(0));
  await sleep(700);
  await glide(page.locator(".files .file").nth(2));
  await sleep(1400);
});

await browser.close();
