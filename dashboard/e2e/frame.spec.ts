import { expect, test } from "@playwright/test";

import { expectAccessible, expectNoSideScroll, noTour, shot } from "./helpers";

const PAGES = ["/", "/market/ACME", "/backtests", "/backtests/showcase-xsmom", "/backtests/new", "/how-it-works",
               "/glossary", "/about", "/no-such-page"];

test("every page carries the simulated-market banner and passes axe", async ({ page }) => {
  await noTour(page);
  for (const path of PAGES) {
    await page.goto(path);
    await expect(page.locator(".banner")).toHaveText("Simulated market");
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
  await page.getByRole("button", { name: "Next" }).click();
  await expect(tour).toContainText("2 / 5");
  await page.keyboard.press("Escape");
  await expect(tour).toHaveCount(0);
  await page.reload();
  await page.waitForTimeout(1500);
  await expect(tour).toHaveCount(0); // once per tab
  await page.getByRole("button", { name: "Take the 30-second tour" }).click();
  await expect(tour).toBeVisible();
});

test("underlined terms explain themselves from the keyboard and the mouse", async ({ page }) => {
  await noTour(page);
  await page.goto("/market/ACME");
  const term = page.getByRole("button", { name: "Spread", exact: true });
  await expect(page.locator(".info")).toHaveCount(0); // no ⓘ icons any more
  await page.keyboard.press("Tab");
  await term.focus();
  await expect(page.getByRole("tooltip")).toContainText("gap between");
  await expect(term).toHaveAttribute("aria-describedby", /.+/);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("tooltip")).toHaveCount(0);
  await page.mouse.move(0, 0);
  await term.hover();
  await expect(page.getByRole("tooltip")).toContainText("gap between");
});

test.describe("on a touch screen at 390 px", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test("a tap opens a term's tooltip; a tap elsewhere or Esc closes it", async ({ page }) => {
    await noTour(page);
    await page.goto("/market/ACME");
    const term = page.getByRole("button", { name: "Spread", exact: true });
    const tip = page.getByRole("tooltip");
    await term.tap();
    await expect(tip).toContainText("gap between");
    await expectNoSideScroll(page);
    const box = (await tip.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(390);
    await shot(page, "phone_tooltip");
    await page.locator("h2", { hasText: "Order book" }).tap(); // elsewhere
    await expect(tip).toHaveCount(0);
    await term.tap();
    await expect(tip).toBeVisible();
    await term.tap(); // the term itself toggles it shut
    await expect(tip).toHaveCount(0);
    await term.tap();
    await page.keyboard.press("Escape");
    await expect(tip).toHaveCount(0);
  });
});

test("the footer credits the author and links to About and the Glossary", async ({ page }) => {
  await noTour(page);
  await page.goto("/");
  const footer = page.locator("footer");
  await expect(footer).toContainText("Built by Ovunda Aguma");
  await expect(footer.getByRole("link", { name: "GitHub" })).toHaveAttribute("href", /github\.com\/ovunda1\/marketforge/);
  await footer.getByRole("link", { name: "Glossary" }).click();
  await expect(page.getByTestId("glossary")).toContainText("Spread");
  await expect(page.getByTestId("glossary")).toContainText("Deflated Sharpe");
  await page.locator("footer").getByRole("link", { name: "About" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("About");
  await expect(page.getByTestId("not-advice")).toHaveText("Backtests use historical data and are not investment advice.");
});

test("OpenAPI docs have an Authorize button for the API key", async ({ page }) => {
  await page.goto("/api/docs");
  await expect(page.getByRole("button", { name: /Authorize/ })).toBeVisible({ timeout: 30_000 });
});
