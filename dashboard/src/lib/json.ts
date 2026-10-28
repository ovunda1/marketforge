// JSON that never rounds an integer silently (M11). A JSON number becomes a double, which
// holds integers exactly only up to 2**53 - 1; a larger integer literal parses to a
// rounded value, which is still an integer above Number.MAX_SAFE_INTEGER. The reviver
// throws on any such value: the gateway sends big integers as strings
// (services/common/safe_json.py), so seeing one means a bug, and it fails loudly.

import { fallbackErrors } from "../copy";

export class UnsafeIntegerError extends Error {
  constructor(public readonly path: string, public readonly value: number) {
    super(fallbackErrors.unsafeInteger(path, String(value)));
    this.name = "UnsafeIntegerError";
  }
}

export function parseJson<T = unknown>(text: string): T {
  return JSON.parse(text, function (this: unknown, key: string, value: unknown) {
    if (typeof value === "number" && Number.isInteger(value) && !Number.isSafeInteger(value)) {
      throw new UnsafeIntegerError(key || "$", value);
    }
    return value;
  }) as T;
}
