import { describe, expect, it } from "vitest";

import { presentQuizText } from "./quizText";

describe("presentQuizText", () => {
  it("turns a source-labeled fallback into a plain question", () => {
    const prompt =
      "Item 2 (remember) for EXERCISE 2.2: which statement is grounded in this source excerpt: EXERCISE 2.2 Solve $$x = 3$$?";
    expect(presentQuizText(prompt)).toBe("Which statement is correct?");
  });

  it("drops the section name from the correct option and PDF symbol glyphs", () => {
    const option =
      "A fact from the WHAT HAVE WE DISCUSSED? excerpt: WHAT HAVE WE DISCUSSED? \uf020 [Diagram] \uf04d $$3x = 9$$";
    expect(presentQuizText(option)).toBe("$$3x = 9$$");
    expect(
      presentQuizText(
        "A fact from the 2.3 Reducing Equations excerpt: 2.3 Reducing Equations to Simpler Form $$7x = -35$$",
      ),
    ).toBe("$$7x = -35$$");
    expect(presentQuizText(option)).not.toMatch(/exercise|discussed|excerpt|[\uE000-\uF8FF]/i);
  });

  it("replaces distractors that name the source heading", () => {
    expect(presentQuizText("An unrelated claim that contradicts EXERCISE 2.2.")).toBe(
      "This reverses the correct steps.",
    );
    expect(presentQuizText("A common mix-up about WHAT HAVE WE DISCUSSED?.")).toBe(
      "This changes the equation in a way that is not allowed.",
    );
  });

  it("drops an equation that was cut off mid-command", () => {
    const option =
      "A fact from the 2.3 excerpt: 2.3 Reducing Equations to Simpler Form $$7x = -35$$ Solution $$x = -\\frac{";
    expect(presentQuizText(option)).toBe("$$7x = -35$$ Solution");
  });

  it("leaves a normal question unchanged", () => {
    expect(presentQuizText("Solve for $x$: $x + 5 = 12$.")).toBe("Solve for $x$: $x + 5 = 12$.");
  });
});
