import { expect, test } from "@playwright/test";
import { statSync } from "node:fs";

// Full first-run journey against the real backend: project → demo source → analysis → generate →
// preview → studio edit → persistence across reload → WAV export → download.

test("create → analyze → generate → studio → export", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));

  await page.goto("/");
  await page.getByTestId("new-project").click();
  await page.getByLabel("Name").fill("E2E Beat");
  await page.getByTestId("create-project").click();
  await expect(page.getByTestId("project-name")).toHaveText("E2E Beat");

  // source + analysis
  await page.getByTestId("use-demo").click();
  await expect(page.getByTestId("analysis-bpm")).toBeVisible({ timeout: 240_000 });
  await expect(page.getByTestId("analysis-bpm")).toHaveText(/\d/);
  await expect(page.getByTestId("source-waveform")).toBeVisible();

  // samples were auto-discovered
  await page.getByTestId("tab-samples").click();
  await expect(page.getByTestId("sample-card").first()).toBeVisible({ timeout: 240_000 });

  // generate a short beat
  await page.getByTestId("tab-generate").click();
  await page.getByTestId("genre-boombap").click();
  await page.getByLabel("Length (bars)").fill("8");
  await page.getByRole("slider", { name: "Variations" }).fill("2");
  await page.getByTestId("generate").click();
  await expect(page.getByTestId("variation-card")).toHaveCount(2, { timeout: 300_000 });

  // preview a variation through the shared player
  await page.getByTestId("variation-play").first().click();
  await expect(page.getByTestId("now-playing")).toBeVisible();

  // open in studio
  await page.getByTestId("open-in-studio").first().click();
  const confirm = page.getByTestId("confirm-open");
  if (await confirm.isVisible().catch(() => false)) await confirm.click();
  await expect(page.getByTestId("studio")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId("clip").first()).toBeVisible();

  // edit: mute a track, toggle a drum step, change tempo
  const mute = page.locator('[data-testid^="mute-"]').first();
  const muteId = await mute.getAttribute("data-testid");
  await mute.click();
  await expect(mute).toHaveAttribute("aria-pressed", "true");
  await page.getByTestId("bpm").fill("92");
  await page.getByTestId("bpm").press("Enter");

  const drumClip = page.locator('[data-lane-id="kick"] [data-testid="clip"], [data-lane-id="hats"] [data-testid="clip"]').first();
  await drumClip.click();
  await expect(page.getByTestId("pattern-editor")).toHaveAttribute("data-kind", "drums");
  const cell = page.getByTestId("drum-cell").nth(3);
  const before = await cell.getAttribute("data-on");
  await cell.click();
  await expect(cell).toHaveAttribute("data-on", before === "true" ? "false" : "true");

  // playback runs and the playhead advances
  await page.getByTestId("play").click();
  await expect(page.getByTestId("position")).not.toHaveText(/0:00\.0$/, { timeout: 30_000 });
  await page.getByTestId("stop").click();

  // undo / redo the drum edit
  await page.getByTestId("undo").click();
  await expect(cell).toHaveAttribute("data-on", before ?? "false");
  await page.getByTestId("redo").click();

  await expect(page.getByTestId("save-state")).toHaveAttribute("data-state", "saved", { timeout: 15_000 });

  // reload: edits persisted
  await page.reload();
  await expect(page.getByTestId("studio")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId(muteId!)).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("bpm")).toHaveValue("92");

  // export WAV and download it
  await page.getByTestId("tab-export").click();
  await page.getByLabel("Format").selectOption("wav24");
  await page.getByTestId("export").click();
  // completed jobs are hidden from the list, so wait for the export's download link instead
  const link = page.getByTestId("download-link").first();
  await expect(link).toBeVisible({ timeout: 240_000 });
  await expect(page.locator('[data-testid="job-export"][data-status="failed"]')).toHaveCount(0);
  const [download] = await Promise.all([page.waitForEvent("download"), link.click()]);
  const path = await download.path();
  expect(download.suggestedFilename()).toMatch(/\.wav$/);
  expect(statSync(path).size).toBeGreaterThan(100_000);

  expect(errors).toEqual([]);
});

test("settings page lists models and kits", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("nav-settings").click();
  await expect(page.getByTestId("model-builtin-hpss")).toBeVisible();
  await page.getByRole("button", { name: "Check database" }).click();
  await expect(page.getByText("Last check: available")).toBeVisible();
});
