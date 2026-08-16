import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const styles = readFileSync(join(process.cwd(), "src/styles.css"), "utf8");

describe("project name layout", () => {
  it("keeps long unbroken project names inside the sidebar heading", () => {
    expect(styles).toMatch(
      /\.sidebar-heading\s*>\s*div\s*\{[^}]*min-width:\s*0[^}]*\}/,
    );
    expect(styles).toMatch(
      /\.sidebar-heading\s+h2\s*\{[^}]*overflow-wrap:\s*anywhere[^}]*\}/,
    );
  });
});
