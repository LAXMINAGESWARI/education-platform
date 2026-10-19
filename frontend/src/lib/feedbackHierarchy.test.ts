import { describe, expect, it } from "vitest";

import { attemptBadge, formatScore, formatTrend } from "./feedbackHierarchy";

describe("feedback hierarchy display", () => {
  it("says not attempted instead of 0%", () => {
    expect(formatScore(null)).toBe("Not attempted");
  });

  it("does not print a signed percent next to the trend word", () => {
    expect(formatTrend("declining", "-100")).toBe("Down 100 points since the previous attempt");
    expect(formatTrend("improving", "0")).toBe("No change since the previous attempt");
    expect(formatTrend(null, "100")).toBeNull();
  });

  it("uses the tiered pass titles and leaves a single miss as keep going", () => {
    expect(attemptBadge("passed", 2, 100)).toBe("💎 Diamond");
    expect(attemptBadge("keep_going", 1, 40)).toBe("Keep going");
    expect(attemptBadge("not_attempted", 0, null)).toBe("Not attempted");
  });
});
