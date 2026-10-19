import { describe, expect, it } from "vitest";

import { formatScore, formatTrend } from "./feedbackHierarchy";

describe("feedback hierarchy display", () => {
  it("says not attempted instead of 0%", () => {
    expect(formatScore(null)).toBe("Not attempted");
  });

  it("does not print a signed percent next to the trend word", () => {
    expect(formatTrend("declining", "-100")).toBe("Down 100 points since the previous attempt");
    expect(formatTrend("improving", "0")).toBe("No change since the previous attempt");
    expect(formatTrend(null, "100")).toBeNull();
  });
});
