import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@playwright/test";

import { expectAccessible, noTour, REPO, shot } from "./helpers";

const replay = JSON.parse(readFileSync(resolve(REPO, "bench/results/replay.json"), "utf8"));
const tsmom = JSON.parse(readFileSync(resolve(REPO, "research/showcase/tsmom/metrics.json"), "utf8"));

test.beforeEach(async ({ page }) => noTour(page));

test("the Overview's numbers are the results files'", async ({ page }) => {
  await page.goto("/");
  const rows = page.getByTestId("perf-rows").locator("tr");
  await expect(rows).toHaveCount(Object.keys(replay.profiles).length);
  for (const [name, p] of Object.entries(replay.profiles as Record<string, any>)) {
    const row = page.getByTestId("perf-rows").locator("tr", { hasText: name });
    await expect(row).toContainText(`${(p.throughput.commands_per_sec.median / 1e6).toFixed(2)} M`);
    await expect(row).toContainText(`${p.latency.p99.median} ns`);
  }
  await expect(page.locator("#perf")).toContainText(replay.machine.cpu);
  await expect(page.locator("#perf")).toContainText(replay.git.commit.slice(0, 7));
  await expect(page.getByTestId("showcase-verdict")).toContainText(`deflated Sharpe ${tsmom.performance.dsr.net.toFixed(2)}`);
  await expect(page.getByTestId("showcase-verdict")).toContainText("Not statistically significant");
  await expect(page.locator("#showcase")).toContainText(tsmom.performance.sharpe.net.toFixed(2));
  await shot(page, "overview");
  await expectAccessible(page, "overview");
});

test("Watch the live market goes to the first fictional symbol", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("watch-market").click();
  await expect(page).toHaveURL(/\/market\/ACME$/);
  await expect(page.getByTestId("conn-state")).toHaveText("Live");
});
