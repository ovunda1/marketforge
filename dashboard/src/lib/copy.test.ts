// Every user-visible sentence lives in copy.ts (M11b), so the wording can be changed in one
// file. This walks every other source file's syntax tree and fails on JSX text with words,
// on words in the attributes people read (aria-label, title, placeholder, alt), and on any
// string literal that holds a phrase (two words or more). Class names, URLs and CSS stay, and
// so do messages for programmers (new Error(...): a bug, never a person's mistake).
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const SKIP = ["copy.ts", "lib/fixtures.ts", "vite-env.d.ts"];
function files(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    if (statSync(p).isDirectory()) return files(p);
    return /\.(ts|tsx)$/.test(f) && !/\.test\.ts$/.test(f) && !SKIP.includes(relative(SRC, p)) ? [p] : [];
  });
}

const READ_ATTRS = new Set(["aria-label", "title", "placeholder", "alt", "label"]);
const CODE_ATTRS = new Set(["className", "d", "viewBox", "href", "to", "type", "id", "key", "name", "role",
                            "data-testid", "inputMode", "autoComplete", "orient", "textAnchor"]);
const PHRASE = /[A-Za-z]{2,}[,.:;]? [A-Za-z(]+/;
const DEV_ERRORS = new Set(["Error", "TypeError", "RangeError"]);

function inDevError(n: ts.Node): boolean {
  for (let p: ts.Node | undefined = n.parent; p && !ts.isBlock(p); p = p.parent) {
    if (ts.isNewExpression(p) && DEV_ERRORS.has(p.expression.getText())) return true;
  }
  return false;
}

function attrName(n: ts.Node): string | null {
  for (let p: ts.Node | undefined = n.parent; p; p = p.parent) {
    if (ts.isJsxAttribute(p)) return p.name.getText();
    if (ts.isJsxElement(p) || ts.isJsxSelfClosingElement(p) || ts.isBlock(p)) return null;
  }
  return null;
}

export function violations(path: string, text: string): string[] {
  const sf = ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true,
                                 path.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const out: string[] = [];
  const report = (n: ts.Node, what: string) => {
    const { line } = sf.getLineAndCharacterOfPosition(n.getStart());
    out.push(`${relative(SRC, path)}:${line + 1} ${what}: ${n.getText().slice(0, 60)}`);
  };
  const visit = (n: ts.Node) => {
    if (ts.isImportDeclaration(n) || ts.isExportDeclaration(n)) return;
    if (ts.isJsxText(n) && /[A-Za-z]{2,}/.test(n.getText())) report(n, "JSX text");
    if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n) || ts.isTemplateHead(n) ||
        ts.isTemplateMiddle(n) || ts.isTemplateTail(n)) {
      const attr = attrName(n);
      const value = n.text;
      if (attr && READ_ATTRS.has(attr) && /[A-Za-z]{2,}/.test(value)) report(n, `${attr}=`);
      else if (!(attr && CODE_ATTRS.has(attr)) && !inDevError(n) && PHRASE.test(value)) report(n, "phrase");
    }
    ts.forEachChild(n, visit);
  };
  visit(sf);
  return out;
}

describe("copy lives in copy.ts", () => {
  it("finds the kinds of text it is meant to find", () => {
    expect(violations("x.tsx", `const a = <p>Hello there</p>;`)).toHaveLength(1);
    expect(violations("x.tsx", `const a = <b aria-label="Close" />;`)).toHaveLength(1);
    expect(violations("x.ts", `const s = "Enter a price.";`)).toHaveLength(1);
    expect(violations("x.ts", "const s = `Steps of ${t}.`;")).toHaveLength(1);
    expect(violations("x.tsx", `const a = <div className="page wide">{C.x} · {n}</div>;`)).toEqual([]);
    expect(violations("x.ts", `const p = "/orders?status=open"; const c = "btn outline small";`)).toHaveLength(1);
    expect(violations("x.ts", "throw new Error(`bad tick size ${t}`);")).toEqual([]);
  });
  it("no other source file holds a sentence", () => {
    const all = files(SRC).flatMap((f) => violations(f, readFileSync(f, "utf8")));
    expect(all).toEqual([]);
  });
});
