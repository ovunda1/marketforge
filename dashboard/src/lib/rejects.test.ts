import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import { codes, explain, explainCancel } from "./rejects";

describe("every reject and error has a human message and a fix", () => {
  it("covers the engine's reject reasons", () => {
    expect(codes.engine).toHaveLength(18);
    for (const reason of codes.engine) {
      const e = explain(new ApiError(422, "ORDER_REJECTED", "rejected", { reason }));
      expect(e.reason).toBe(reason);
      expect(e.title.length).toBeGreaterThan(5);
      expect(e.fix).toMatch(/\.$/);
      expect(e.message).not.toContain(reason);
    }
  });
  it("covers every gateway error code", () => {
    for (const code of codes.api) {
      const e = explain(new ApiError(400, code, "x"));
      expect(e.title).toBeTruthy();
      expect(e.fix).toMatch(/\.$/);
    }
  });
  it("maps validation details to fields", () => {
    const e = explain(new ApiError(422, "VALIDATION_ERROR", "x", { details: [{ field: "end", message: "The data ends on 2024-12-31." }] }));
    expect(e.fields).toEqual([{ field: "end", message: "The data ends on 2024-12-31." }]);
  });
  it("explains cancel reasons and unknown errors", () => {
    expect(explainCancel("unfilled").title).toBe("The rest was cancelled");
    expect(explain(new ApiError(500, "SOMETHING_NEW", "x")).title).toBe("Something went wrong");
    expect(explain(new TypeError("Failed to fetch")).message).toBe("Failed to fetch");
  });
});
