import { describe, expect, it } from "vitest";

import { normalizeMarkdownMath } from "./markdownMath";

describe("normalizeMarkdownMath", () => {
  it("converts escaped LaTeX delimiters to dollar math", () => {
    expect(normalizeMarkdownMath(String.raw`Solve \(x + 2 = 5\).`)).toBe("Solve $x + 2 = 5$.");
    expect(normalizeMarkdownMath(String.raw`Display \[a^2 + b^2 = c^2\] done.`)).toBe(
      "Display $$a^2 + b^2 = c^2$$ done.",
    );
  });

  it("lifts indented display math out of list continuations", () => {
    const source = ["- Odd sums:", "  $$1 + 3 = 4 = 2^2$$", "more"].join("\n");
    const normalized = normalizeMarkdownMath(source);
    expect(normalized).toContain("$$1 + 3 = 4 = 2^2$$");
    expect(normalized).not.toMatch(/^[ \t]+\$\$/m);
  });

  it("trims spaces inside dollar delimiters so KaTeX can parse them", () => {
    expect(normalizeMarkdownMath("Solve for $x $. Then $$2x = 8 $$.")).toBe(
      "Solve for $x$. Then $$2x = 8$$.",
    );
  });

  it("converts double-escaped LLM delimiters and commands", () => {
    const source = [
      String.raw`Let's look at the equation \\(2x - 3 = 9\\).`,
      "",
      String.raw`     \\[`,
      String.raw`     2x - 3 + 3 = 9 + 3 \\implies 2x = 12`,
      String.raw`     \\]`,
    ].join("\n");
    const normalized = normalizeMarkdownMath(source);
    expect(normalized).toContain("$2x - 3 = 9$");
    expect(normalized).toContain("$$2x - 3 + 3 = 9 + 3 \\implies 2x = 12$$");
    expect(normalized).not.toContain("\\\\(");
    expect(normalized).not.toContain("\\\\[");
  });

  it("unwraps a document-level markdown fence before converting math", () => {
    const source = [
      "```markdown",
      String.raw`Solve \\(x + 2 = 5\\).`,
      "",
      "```mermaid",
      "graph TD",
      "  A --> B",
      "```",
      "```",
    ].join("\n");
    const normalized = normalizeMarkdownMath(source);
    expect(normalized).toContain("$x + 2 = 5$");
    expect(normalized).toContain("```mermaid");
    expect(normalized.startsWith("```markdown")).toBe(false);
  });

  it("unwraps lesson json and drops the grade-voice instruction", () => {
    const source = [
      "# Lesson",
      "",
      "Write for Grade 8 students studying hello. Use plain language.",
      "",
      "## Introduction",
      "Already taught.",
      "",
      '{"lesson_markdown": "## Solving\\n\\nUse \\(2x - 3 = 7\\)."}',
    ].join("\n");
    const normalized = normalizeMarkdownMath(source);
    expect(normalized).toContain("## Solving");
    expect(normalized).toContain("$2x - 3 = 7$");
    expect(normalized).not.toContain("lesson_markdown");
    expect(normalized).not.toContain("studying hello");
  });

  it("restores JSON-eaten LaTeX commands and collapses quadruple dollars", () => {
    const normalized = normalizeMarkdownMath("Area $2 \\\u000crac{5}{4}$ and $$$$x + 2 = 10$$$$.");
    expect(normalized).toContain("$2 \\frac{5}{4}$");
    expect(normalized).toContain("$$x + 2 = 10$$");
    expect(normalized).not.toContain("\u000c");
  });

  it("moves an align environment onto its own line and restores row breaks", () => {
    const source = "$$\\begin{align*}\n2x - 3 &= 4 \\\n\\end{align*}$$";
    const normalized = normalizeMarkdownMath(source);
    expect(normalized).toContain("$$\n\\begin{align*}");
    expect(normalized).toContain("2x - 3 &= 4 \\\\\n");
  });

  it("strips undecoded Docling formula comments", () => {
    expect(normalizeMarkdownMath("Solve <!-- formula-not-decoded --> next.")).toBe("Solve  next.");
  });

  it("leaves mermaid fences unchanged", () => {
    const source = ["```mermaid", "graph TD", String.raw`  A[x + 2 = 5] --> B`, "```"].join("\n");
    expect(normalizeMarkdownMath(source)).toBe(source);
  });
});
