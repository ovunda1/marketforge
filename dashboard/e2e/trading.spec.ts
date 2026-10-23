import { expect, test, type Page } from "@playwright/test";

import { expectAccessible, noTour, shot, startPractice } from "./helpers";

test.beforeEach(async ({ page }) => noTour(page));

async function bestBid(page: Page): Promise<number> {
  const book = JSON.parse(await page.evaluate(async () => (await fetch("/api/book/ACME?depth=1")).text()));
  return book.bids[0][0] as number;
}

const fmt = (ticks: number) => {
  const s = String(ticks).padStart(3, "0");
  return `${s.slice(0, -2)}.${s.slice(-2)}`;
};

test("try trading: rest an order, cancel it, then fill and hold a position", async ({ page }) => {
  await page.goto("/market/ACME");
  await expect(page.getByTestId("place-order")).toHaveCount(0); // no account yet
  await startPractice(page);
  await expect(page.getByTestId("place-order")).toBeVisible();
  // The price is pre-filled from the market (a buy: the best ask).
  await expect(page.locator("#price")).toHaveValue(/^\d+\.\d{2}$/);

  // A buy far below the market rests in the book.
  const low = (await bestBid(page)) - 300;
  await page.locator("#price").fill(fmt(low));
  await expect(page.getByTestId("order-summary")).toContainText(`at $${fmt(low)} or less`);
  await expect(page.getByTestId("order-summary")).toContainText("won't fill yet");
  await page.getByTestId("place-order").click();
  await expect(page.getByTestId("order-result")).toContainText("In the book");
  const orders = page.getByTestId("account-orders");
  await expect(orders).toContainText(fmt(low));
  await orders.getByRole("button", { name: /^Cancel buy/ }).click();
  await expect(orders).toContainText("No open orders.");

  // A market buy fills against the bot's asks.
  await page.locator("label", { hasText: "Market" }).click();
  await expect(page.getByTestId("order-summary")).toContainText("at the best prices available now");
  await page.getByTestId("place-order").click();
  await expect(page.getByTestId("order-result")).toContainText(/Filled|Partly filled|Bought/);
  await page.getByRole("tab", { name: "Fills" }).click();
  await expect(page.getByTestId("account-fills")).toContainText("▲ bought");
  await page.getByRole("tab", { name: "Positions" }).click();
  await expect(page.getByTestId("account-positions")).toContainText("ACME");
  await expect(page.getByTestId("pnl-ACME")).toHaveText(/\$\d/);
  await shot(page, "market-trading");
  await expectAccessible(page, "market with an account");
});

test("rejects explain themselves and suggest a fix", async ({ page }) => {
  await page.goto("/market/ACME");
  await startPractice(page);
  // Off the tick grid: caught as you type, with the two nearest valid prices.
  await page.locator("#price").fill("41.995");
  await expect(page.locator("#price-hint")).toHaveText("Prices move in steps of 0.01. Try 41.99 or 42.00.");
  // Above the instrument's max_qty (deploy/config/risk.yaml): the engine rejects it.
  await page.locator("#price").fill(fmt((await bestBid(page)) - 300));
  await page.locator("#qty").fill("200000");
  await page.getByTestId("place-order").click();
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("That quantity is above the limit");
  await expect(alert).toContainText("What to do: Split it into smaller orders.");
  await expect(alert).not.toContainText("MAX_QTY"); // codes only with technical details on
  // A fill-or-kill that can't fill completely.
  await page.locator("#qty").fill("10");
  await page.locator("#tif").selectOption("fok");
  await expect(page.getByTestId("order-summary")).toContainText("so it would be rejected without trading");
  await page.getByTestId("place-order").click();
  await expect(page.getByRole("alert")).toContainText("It couldn't fill completely, so nothing traded");
});

test("trading works from the keyboard alone", async ({ page }) => {
  await page.goto("/market/ACME");
  await startPractice(page);
  const reach = async (selector: string) => {
    for (let i = 0; i < 80; i++) {
      if (await page.evaluate((s) => document.activeElement?.matches(s) ?? false, selector)) return;
      await page.keyboard.press("Tab");
    }
    throw new Error(`could not tab to ${selector}`);
  };
  await page.locator("body").click({ position: { x: 5, y: 5 } });
  await reach("input[name=side][value=buy]");
  await page.keyboard.press("ArrowRight"); // radio group: Sell
  await expect(page.locator("input[name=side][value=sell]")).toBeChecked();
  await page.keyboard.press("ArrowLeft");
  await reach("#qty");
  await page.keyboard.press("ControlOrMeta+a");
  await page.keyboard.type("5");
  await reach("#price");
  await page.keyboard.press("ControlOrMeta+a");
  await page.keyboard.type(fmt((await bestBid(page)) - 400));
  await reach("[data-testid=place-order]");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("order-result")).toContainText("In the book");
  await reach("button[aria-label^='Cancel buy']");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("account-orders")).toContainText("No open orders.");
});

test("the API key lives in sessionStorage, never localStorage", async ({ page }) => {
  await page.goto("/market/ACME");
  await startPractice(page);
  const stored = await page.evaluate(() => ({ session: window.sessionStorage.getItem("mf.session"),
                                              localKeys: Object.keys(window.localStorage) }));
  expect(JSON.parse(stored.session!).apiKey).toMatch(/^mf_[0-9a-f]{8}_/);
  expect(stored.localKeys).toEqual([]);
  await page.reload(); // survives a reload in the same tab
  await expect(page.locator(".account-chip")).toContainText("practice-");
  const other = await page.context().newPage(); // not shared with another tab
  await other.goto("/market/ACME");
  await expect(other.locator(".account-chip")).toHaveCount(0);
});
