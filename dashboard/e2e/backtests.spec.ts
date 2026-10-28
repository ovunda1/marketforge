import { expect, test } from "@playwright/test";

import { expectAccessible, noTour, shot } from "./helpers";

test.beforeEach(async ({ page }) => noTour(page));

test("the list shows the showcase runs, labelled as historical data, and every run counted", async ({ page }) => {
  await page.goto("/backtests");
  const rows = page.getByTestId("run-row");
  await expect(rows.first()).toBeVisible();
  await expect(page.locator("#featured")).toContainText("historical data");
  await expect(page.getByTestId("overfitting-note")).toContainText("Every run is recorded");
  await expect(page.getByTestId("total-runs")).toHaveText(/^\d+$/);
  await expect(page.getByTestId("not-advice")).toHaveText("Backtests use historical data and are not investment advice.");
  await shot(page, "backtests");
  await expectAccessible(page, "backtests");
});

test("a featured run leads with its plain-language summary", async ({ page }) => {
  await page.goto("/backtests/showcase-tsmom");
  await expect(page.getByTestId("run-summary")).toContainText("That is not statistically significant");
  await expect(page.locator(".meta")).toContainText("historical data");
  await expect(page.getByTestId("gross-net")).toContainText("Sharpe ratio");
  await expect(page.locator("#costs svg")).toBeVisible();
  await expect(page.getByTestId("not-advice")).toBeVisible();
  await shot(page, "backtest-showcase");
  await expectAccessible(page, "backtest result");
});

test("run a backtest from the form and watch it to the result", async ({ page }) => {
  test.setTimeout(240_000);
  await page.goto("/backtests/new");
  await page.getByTestId("try-trading").click();
  await expect(page.locator(".account-chip")).toContainText("practice-");
  await page.locator("#dataset").selectOption("synthetic");
  // Validation as you type: too short a range for the training window.
  await page.locator("#start").fill("2020-01-02");
  await expect(page.getByRole("alert").first()).toContainText("walk-forward with 3 year(s) of training");
  await page.locator("#start").fill("2012-01-03");
  await page.locator("#train").selectOption("2");
  await page.getByRole("button", { name: "Clear" }).click();
  for (const s of ["AAA", "BBB", "CCC"]) await page.locator("label", { hasText: new RegExp(`^${s}$`) }).click();
  await page.locator("label", { hasText: /^120$/ }).click(); // one lookback: 60
  await expect(page.getByTestId("grid-size")).toHaveText("1 of at most 12");
  await shot(page, "backtest-form");
  await expectAccessible(page, "backtest form");
  await page.getByTestId("run-backtest").click();
  await expect(page).toHaveURL(/\/backtests\/[0-9a-f]{16}$/);
  await expect(page.getByTestId("progress")).toBeVisible();
  await expect(page.getByTestId("run-summary")).toContainText("After costs, this strategy", { timeout: 200_000 });
  await expect(page.getByTestId("run-summary")).toContainText("statistically significant");
  await expect(page.locator("#equity svg.chart")).toBeVisible();
  await expect(page.locator("#drawdown svg.chart")).toBeVisible();
  await expect(page.locator("#costs")).toContainText("Commission");
  await expect(page.getByTestId("gross-net")).toContainText("Deflated Sharpe ratio");
  await expect(page.getByTestId("overfitting-note")).toContainText("backtest");
  await expect(page.locator(".meta")).toContainText("synthetic data");
  await shot(page, "backtest-run");
});

test("a practice account may have only two backtests running", async ({ page, request }) => {
  await page.goto("/backtests/new");
  await page.getByTestId("try-trading").click();
  await expect(page.locator(".account-chip")).toContainText("practice-");
  const key = JSON.parse((await page.evaluate(() => window.sessionStorage.getItem("mf.session")))!).apiKey;
  const catalog = await (await request.get("/api/backtests/catalog")).json();
  const ds = catalog.datasets.find((d: { id: string }) => d.id === "synthetic");
  const form = { dataset: "synthetic", strategy: "tsmom", start: ds.start, end: ds.end, symbols: ["AAA", "BBB"],
                 train_years: 1, costs: false,
                 params: { lookback: [60, 120], vol_window: [20, 60], target_vol: [0.05, 0.1, 0.15], max_leverage: [1.0] } };
  const post = () => request.post("/api/backtests", { data: { form }, headers: { "X-API-Key": key } });
  expect((await post()).status()).toBe(202);
  expect((await post()).status()).toBe(202);
  const third = await post();
  expect(third.status()).toBe(429);
  expect((await third.json()).error.code).toBe("BACKTEST_LIMIT");
  // The form shows it as a sentence with a fix.
  await page.getByTestId("run-backtest").click();
  await expect(page.getByRole("alert").last()).toContainText("You have backtests running");
  await expect(page.getByRole("alert").last()).toContainText("Wait for one to finish");
});
