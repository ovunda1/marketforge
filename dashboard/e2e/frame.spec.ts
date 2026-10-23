import { expect, test } from "@playwright/test";

import { expectAccessible, expectNoSideScroll, noTour, shot } from "./helpers";

const PAGES = ["/", "/market/ACME", "/backtests", "/backtests/showcase-xsmom", "/backtests/new", "/how-it-works"];

test("every page carries the simulated-market banner and passes axe", async ({ page }) => {
  await noTour(page);
  for (const path of PAGES) {
    await page.goto(path);
    await expect(page.locator(".banner")).toHaveText(/Simulated market.*practice money only/);
    await page.waitForLoadState("networkidle").catch(() => undefined);
    await expectAccessible(page, path);
  }
});

test("phone width: no sideways scroll, still accessible", async ({ page }) => {
  await noTour(page);
  await page.setViewportSize({ width: 375, height: 812 });
  for (const path of PAGES) {
    await page.goto(path);
    await page.waitForTimeout(800);
    await expectNoSideScroll(page);
    await expectAccessible(page, `${path} at 375 px`);
    await shot(page, `phone${path.replace(/\//g, "_") || "_overview"}`);
  }
});

test("the 30-second tour: shown once, keyboard-skippable, accessible", async ({ page }) => {
  await page.goto("/market/ACME");
  const tour = page.getByTestId("tour");
  await expect(tour).toBeVisible();
  await expect(tour).toContainText("1 / 5");
  await expect(page.locator("#book")).toHaveClass(/tour-target/);
  await expectAccessible(page, "tour open");
  await shot(page, "market-tour");
  await page.getByRole("button", { name: "Next →" }).click();
  await expect(tour).toContainText("2 / 5");
  await page.keyboard.press("Escape");
  await expect(tour).toHaveCount(0);
  await page.reload();
  await page.waitForTimeout(1500);
  await expect(tour).toHaveCount(0); // once per tab
  await page.getByRole("button", { name: "Take the 30-second tour" }).click();
  await expect(tour).toBeVisible();
});

test("tooltips explain trading terms and open from the keyboard", async ({ page }) => {
  await noTour(page);
  await page.goto("/market/ACME");
  const info = page.getByRole("button", { name: "What is the spread?" });
  await info.focus();
  await expect(page.getByRole("tooltip")).toContainText("gap between");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
});

test("OpenAPI docs have an Authorize button for the API key", async ({ page }) => {
  await page.goto("/api/docs");
  await expect(page.getByRole("button", { name: /Authorize/ })).toBeVisible({ timeout: 30_000 });
});
