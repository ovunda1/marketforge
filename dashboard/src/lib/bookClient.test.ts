import { describe, expect, it } from "vitest";

import { BookClient, type MdMessage } from "./bookClient";
import { fixture } from "./fixtures";

interface Step {
  message: MdMessage;
  expect: { result: boolean; synced: boolean; epoch: number | null; md_seq: number; resnapshots: number;
            bids: number[][]; asks: number[][] };
}
const cases = fixture<{ cases: { name: string; symbol: string; steps: Step[] }[] }>("marketdata/cases.json").cases;

describe("BookClient agrees with services/client.py on the shared fixtures", () => {
  it("has the fixtures", () => expect(cases.length).toBeGreaterThanOrEqual(7));
  for (const c of cases) {
    it(c.name, () => {
      const client = new BookClient(c.symbol);
      c.steps.forEach((step, i) => {
        const result = client.onMessage(step.message);
        expect({ result, synced: client.synced, epoch: client.epoch, md_seq: client.mdSeq,
                 resnapshots: client.resnapshots, bids: client.bids(), asks: client.asks() },
               `step ${i}`).toEqual(step.expect);
      });
    });
  }
});
