import { describe, expect, it } from "vitest";

import { delimitBareLatex } from "./delimitBareLatex";

describe("delimitBareLatex", () => {
  it("wraps a trapezoid formula that has no dollar signs", () => {
    expect(
      delimitBareLatex("Area = \\frac{1}{2} \\times (base_1 + base_2) \\times height"),
    ).toBe("$Area = \\frac{1}{2} \\times (base_1 + base_2) \\times height$");
  });

  it("wraps bare fraction, division, and comparison commands", () => {
    expect(delimitBareLatex("Volume = \\frac{1}{3} \\times base \\times height")).toBe(
      "$Volume = \\frac{1}{3} \\times base \\times height$",
    );
    expect(delimitBareLatex("Share \\div 2")).toBe("Share $\\div 2$");
    expect(delimitBareLatex("Use \\le, \\ge, \\neq, and \\pi.")).toBe(
      "Use $\\le, \\ge, \\neq$, and $\\pi$.",
    );
  });

  it("wraps roots, dots, text, and scripts without swallowing the sentence", () => {
    expect(delimitBareLatex("The length is \\sqrt{4} \\cdot x^{2} in \\text{cm}.")).toBe(
      "The length is $\\sqrt{4} \\cdot x^{2}$ in $\\text{cm}$.",
    );
    expect(delimitBareLatex("Label y_{1} on the figure.")).toBe("Label $y_{1}$ on the figure.");
  });

  it("does not double-wrap math that is already delimited", () => {
    const inline = "Solve for $x$: $x + 5 = 12$.";
    expect(delimitBareLatex(inline)).toBe(inline);
    const mixed = "Use $\\frac{1}{2}$ or \\frac{1}{3}.";
    expect(delimitBareLatex(mixed)).toBe("Use $\\frac{1}{2}$ or $\\frac{1}{3}$.");
    const display = "$$\\frac{1}{2} \\times h$$";
    expect(delimitBareLatex(display)).toBe(display);
  });

  it("leaves ordinary sentences unchanged", () => {
    expect(delimitBareLatex("A rectangle with equal sides")).toBe("A rectangle with equal sides");
  });
});
