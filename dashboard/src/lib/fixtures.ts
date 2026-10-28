// Test helper: the shared fixtures under tests/fixtures/ (the Python suite reads the same files).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export function fixture<T>(path: string): T {
  return JSON.parse(readFileSync(fileURLToPath(new URL(`../../../tests/fixtures/${path}`, import.meta.url)), "utf8")) as T;
}
