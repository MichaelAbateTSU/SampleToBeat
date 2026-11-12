// End-to-end check against a running server: guest makes a beat, signs up, hits the paywall, upgrades (mock
// billing), edits the mix and exports WAV + stems + MIDI.
//   CHOPLAB_BILLING=mock uv run choplab   (in backend/)        then:   node e2e/smoke.mjs [--shots dir]
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";

const { chromium } = createRequire(import.meta.url)("playwright"); // works with a global or local install

const BASE = process.env.BASE_URL ?? "http://localhost:8765";
const shotsArg = process.argv.indexOf("--shots");
const SHOTS = shotsArg > 0 ? process.argv[shotsArg + 1] : null;
if (SHOTS) mkdirSync(SHOTS, { recursive: true });

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: SHOTS ? 2 : 1 });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
// 401/402 are the sign-up wall and the paywall doing their job; anything else is a real error
page.on("console", (m) => m.type() === "error" && !/status of 40[12]/.test(m.text()) && errors.push(`console: ${m.text()}`));
page.on("response", (r) => r.status() >= 500 && errors.push(`${r.status()} ${r.url()}`));
const shot = async (name, opts = {}) => SHOTS && page.screenshot({ path: `${SHOTS}/${name}.png`, ...opts });
const step = (s) => console.log(`✓ ${s}`);
const expect = (cond, msg) => { if (!cond) throw new Error(`FAILED: ${msg}`); };

try {
  // landing
  await page.goto(BASE);
  await page.getByRole("heading", { name: /Drop a sample/ }).waitFor();
  expect(await page.locator(".plan").count() === 3, "three plans on the landing page");
  expect(await page.locator(".pad-btn").count() === 5, "four flip pads and the source sample");
  await page.locator(".pad-btn").first().click();
  await page.locator(".pad-btn.on").waitFor();
  await page.waitForFunction(() => /0:0[1-9]/.test(document.querySelector(".machine-lcd .time")?.textContent ?? ""), null, { timeout: 20_000 });
  await shot("01-landing-playing");
  await page.locator(".pad-btn").first().click();
  await shot("01-landing");
  await shot("01-landing-full", { fullPage: true });
  step("landing renders; a hero pad plays a demo flip");

  // guest -> demo loop -> create
  await page.getByRole("button", { name: /Try the demo loop/ }).click();
  await page.waitForURL(/\/app\/new/);
  await page.getByText("Demo loop:").waitFor();
  await page.getByRole("button", { name: /Trap/ }).click();
  await shot("02-create");
  await page.getByRole("button", { name: /Make my beat/ }).click();
  await page.waitForURL(/\/app\/p\//);
  await page.locator(".progress-card").waitFor();
  await page.waitForTimeout(2500);
  await shot("03-progress");
  step("beat job started");

  await page.locator(".variation").first().waitFor({ timeout: 180_000 });
  expect(await page.locator(".variation").count() === 4, "four flips");
  await page.locator(".timeline").waitFor();
  expect(await page.locator(".tl-clip").count() > 10, "arrangement has clips");
  await shot("04-results");
  step("four flips rendered and the best one opened in the studio");

  // playback
  await page.locator(".variation .play-btn").first().click();
  await page.locator(".playerbar").waitFor();
  await page.waitForFunction(() => /0:0[1-9]/.test(document.querySelector(".playerbar-time")?.textContent ?? ""), null, { timeout: 20_000 });
  await shot("05-playing");
  step("audio plays");
  await page.locator(".playerbar .play-btn").click();

  // export as a guest -> signup wall
  await page.getByRole("tab", { name: /Export/ }).click();
  await shot("06-export-free");
  await page.getByRole("button", { name: /Export beat/ }).click();
  await page.getByRole("dialog", { name: /Save your beat/ }).waitFor();
  await shot("07-signup");
  const email = `e2e-${Date.now()}@example.com`;
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("a good long password");
  await page.getByRole("button", { name: "Create free account" }).click();
  await page.locator(".files .file").first().waitFor({ timeout: 60_000 });
  expect(await page.locator(".files .file").count() === 2, "free export: mp3 + license");
  const [dl] = await Promise.all([page.waitForEvent("download"), page.locator(".files .file").first().click()]);
  expect(dl.suggestedFilename().endsWith(".mp3"), `free download is mp3 (got ${dl.suggestedFilename()})`);
  step("signup wall → account → MP3 export downloads");

  // paywall -> mock checkout -> pro
  await page.locator(".format", { hasText: "WAV 24-bit" }).click();
  await page.getByRole("dialog", { name: /Unlock with Pro/ }).waitFor();
  await shot("08-upgrade");
  await page.getByRole("button", { name: /Get Pro/ }).click();
  await page.waitForURL(/\/app\/account\?upgraded=1/);
  await page.getByText(/You're on Pro/).waitFor();
  await shot("09-account");
  step("paywall → checkout → Pro plan active");

  // library -> project -> edit mix -> export wav + stems + midi
  await page.getByRole("link", { name: "My beats" }).click();
  await page.locator(".beat").first().waitFor();
  await shot("10-library");
  await page.locator(".beat-main").first().click();
  await page.locator(".timeline").waitFor();
  // keyboard: the skip link is the first stop, and dialogs trap focus and close on Escape
  await page.reload();
  await page.locator(".timeline").waitFor();
  await page.keyboard.press("Tab");
  expect(await page.evaluate(() => document.activeElement?.textContent) === "Skip to content", "skip link is first in tab order");
  await page.locator(".variation .btn-icon").first().click();
  await page.getByRole("dialog", { name: "Delete this flip?" }).waitFor();
  expect(await page.evaluate(() => document.activeElement?.textContent) === "Keep it", "confirm dialog focuses the safe choice");
  await page.keyboard.press("Escape");
  expect(await page.locator(".variation").count() === 4, "escape cancels the delete");
  await page.getByRole("button", { name: "Mute Hi-hats" }).click();
  await page.getByLabel("Swing").fill("58");
  await page.getByRole("button", { name: /Apply & listen/ }).click();
  await page.getByRole("button", { name: /Play mix/ }).waitFor({ timeout: 90_000 });
  await page.locator(".tl-playhead").waitFor();
  await shot("11-studio");
  step("mix edit saved, re-rendered and playing with a playhead");
  await page.locator(".playerbar .play-btn").click();

  await page.getByRole("tab", { name: /Export/ }).click();
  await page.locator(".format", { hasText: "WAV 24-bit" }).click();
  await page.locator(".toggle-row", { hasText: "Track stems" }).click();
  await page.locator(".toggle-row", { hasText: "MIDI" }).click();
  await page.getByRole("button", { name: /Export beat/ }).click();
  await page.locator(".files .file").nth(3).waitFor({ timeout: 120_000 });
  const names = await page.locator(".files .file small").allTextContents();
  expect(names.some((n) => n.endsWith(".wav")) && names.some((n) => n.endsWith("_stems.zip")) && names.some((n) => n.endsWith(".mid")) && names.some((n) => n.endsWith("LICENSE.txt")), `pro export files: ${names}`);
  await shot("12-export-pro");
  step(`pro export: ${names.join(", ")}`);

  // sample tab + mobile layout
  await page.getByRole("tab", { name: /Sample/ }).click();
  await page.locator(".samples li").first().waitFor();
  await shot("13-sample");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(BASE);
  await page.getByRole("heading", { name: /Drop a sample/ }).waitFor();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow <= 1, `no horizontal scroll on mobile (overflow ${overflow}px)`);
  await shot("14-mobile");
  step("mobile layout has no horizontal overflow");

  expect(errors.length === 0, `browser errors:\n${errors.join("\n")}`);
  console.log("\nALL GOOD");
} catch (e) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/failure.png` });
  console.error(String(e), errors.length ? `\nbrowser errors:\n${errors.join("\n")}` : "");
  process.exitCode = 1;
} finally {
  await browser.close();
}
