import { execSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

const HERE = dirname(fileURLToPath(import.meta.url));
export const REPO = resolve(HERE, "..", "..");
export const SHOTS = resolve(HERE, "..", "test-results", "screenshots");
const COMPOSE = process.env.MF_COMPOSE
  ?? "docker compose --env-file .env -p mf-test -f deploy/docker-compose.yml -f deploy/docker-compose.test.yml";

export function compose(args: string): string {
  return execSync(`${COMPOSE} ${args}`, { cwd: REPO, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
}

/** Most tests skip the guided tour; the tour test turns it back on. */
export async function noTour(page: Page): Promise<void> {
  await page.addInitScript(() => window.sessionStorage.setItem("mf.tour.seen", "1"));
}

export async function shot(page: Page, name: string): Promise<void> {
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

/** No serious or critical WCAG 2.1 A/AA violations. */
export async function expectAccessible(page: Page, what: string): Promise<void> {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(", ")}`), what).toEqual([]);
}

export async function expectNoSideScroll(page: Page): Promise<void> {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
}

export async function startPractice(page: Page): Promise<void> {
  await page.getByTestId("try-trading").click();
  await expect(page.locator(".account-chip")).toContainText("practice-");
}

/** The live ladder, as the gateway's /book would list it: [price text, qty] per side. */
export async function ladder(page: Page): Promise<{ mdSeq: string; bids: string[][]; asks: string[][] }> {
  return page.evaluate(() => {
    const rows = (sel: string) => [...document.querySelectorAll(sel)].map((tr) =>
      [tr.querySelector("th")!.textContent!.replace("●", "").trim(), tr.querySelector(".q")!.textContent!.replace(/,/g, "")]);
    return { mdSeq: document.querySelector("[data-testid=md-seq]")!.textContent!.replace(/,/g, ""),
             bids: rows("[data-testid=bid-row]"), asks: rows("[data-testid=ask-row]").reverse() };
  });
}
