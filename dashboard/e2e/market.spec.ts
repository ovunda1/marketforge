import { expect, test, type Page } from "@playwright/test";

import { compose, expectAccessible, ladder, noTour, shot } from "./helpers";

test.beforeEach(async ({ page }) => noTour(page));

const fmt = (ticks: number) => { // tick 0.01, as text: no floating point
  const s = String(ticks).padStart(3, "0");
  return `${s.slice(0, -2)}.${s.slice(-2)}`;
};

/** The page's ladder equals GET /book at the same md_seq (retried: the bot keeps trading). */
async function ladderMatchesBook(page: Page): Promise<void> {
  await expect.poll(async () => {
    const before = await ladder(page);
    const book = await page.evaluate(async () => (await fetch("/api/book/ACME?depth=8")).text());
    const after = await ladder(page);
    const b = JSON.parse(book);
    if (String(b.md_seq) !== before.mdSeq || before.mdSeq !== after.mdSeq) return "moved";
    const want = { bids: b.bids.map((l: number[]) => [fmt(l[0]!), String(l[1])]),
                   asks: b.asks.map((l: number[]) => [fmt(l[0]!), String(l[1])]) };
    return JSON.stringify({ bids: before.bids, asks: before.asks }) === JSON.stringify(want) ? "equal" : "different";
  }, { timeout: 30_000, intervals: [100, 200, 300] }).toBe("equal");
}

test("the ladder matches the gateway's book, and the tape grows", async ({ page }) => {
  await page.goto("/market/ACME");
  await expect(page.getByTestId("conn-state")).toHaveText("Live");
  await expect(page.getByTestId("bid-row").first()).toBeVisible();
  await ladderMatchesBook(page);
  const first = await page.getByTestId("tape").locator("tr").first().textContent();
  await expect.poll(async () => page.getByTestId("tape").locator("tr").first().textContent(), { timeout: 30_000 })
    .not.toBe(first);
  await expect(page.getByTestId("last-price")).toHaveText(/^\d+\.\d{2}$/);
  await shot(page, "market");
  await expectAccessible(page, "market");
});

test("a matcher restart (new epoch) resynchronises the page", async ({ page }) => {
  await page.goto("/market/ACME");
  await expect(page.getByTestId("conn-state")).toHaveText("Live");
  const epoch = Number(await page.getByTestId("epoch").textContent());
  compose("restart matcher");
  await expect.poll(async () => Number(await page.getByTestId("epoch").textContent()), { timeout: 60_000 })
    .toBeGreaterThan(epoch);
  await expect(page.getByTestId("conn-state")).toHaveText("Live", { timeout: 30_000 });
  await ladderMatchesBook(page);
});

test("technical details stay behind the toggle", async ({ page }) => {
  await page.goto("/market/ACME");
  await expect(page.getByTestId("status-strip")).toContainText("seq_no");
  await expect(page.getByTestId("tech-details")).toHaveCount(0);
  await expect(page.getByTestId("tape").locator("tr").first().locator("td")).toHaveCount(3);
  await page.getByTestId("tech-toggle").click();
  await expect(page.getByTestId("tech-details")).toContainText("Messages");
  await expect(page.getByTestId("tape").locator("tr").first().locator("td")).toHaveCount(4); // seq_no column
  await expect.poll(async () => page.getByTestId("rtt").textContent(), { timeout: 15_000 }).toMatch(/ms$/);
  await page.getByTestId("tech-toggle").click();
  await expect(page.getByTestId("tech-details")).toHaveCount(0);
});

test("the live market never shows a real ticker", async ({ page }) => {
  const universe = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "LQD", "HYG", "GLD", "SLV", "DBC", "VNQ", "XLB",
                    "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY", "AAPL", "MSFT", "BTCUSD"];
  for (const sym of ["ACME", "GLOBEX", "INITECH"]) {
    await page.goto(`/market/${sym}`);
    await expect(page.getByTestId("conn-state")).toHaveText("Live");
    const text = await page.locator("body").innerText();
    for (const t of universe) expect(text, `${t} on /market/${sym}`).not.toMatch(new RegExp(`\\b${t}\\b`));
    await expect(page.locator(".banner")).toContainText("Simulated market");
    await expect(page.locator(".chip").first()).toContainText("fictional");
  }
});
