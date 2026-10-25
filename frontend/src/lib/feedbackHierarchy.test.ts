import { describe, expect, it } from "vitest";

import {
  PASS_PERCENT,
  attemptBadge,
  attemptTitle,
  focusSentence,
  formatDuration,
  formatScore,
  formatTrend,
  momentumGain,
  topicsNeedingAttention,
  weakTopicNames,
} from "./feedbackHierarchy";

describe("feedback hierarchy display", () => {
  it("says not attempted instead of 0%", () => {
    expect(formatScore(null)).toBe("Not attempted");
  });

  it("does not print a signed percent next to the trend word", () => {
    expect(formatTrend("declining", "-100")).toBe("Down 100 points since the previous attempt");
    expect(formatTrend("improving", "0")).toBe("No change since the previous attempt");
    expect(formatTrend(null, "100")).toBeNull();
  });

  it("turns a stored duration into minutes and leaves sub-minute attempts short", () => {
    expect(formatDuration(600)).toBe("10 min");
    expect(formatDuration(30)).toBe("Under a minute");
    expect(formatDuration(90)).toBe("1 min");
    expect(formatDuration(5400)).toBe("1 h 30 min");
  });

  it("says just only when the topic goal is 10 points away or closer", () => {
    expect(focusSentence(10, "Fractions")).toBe("You're just 10% from your goal on Fractions.");
    expect(focusSentence(11, "Fractions")).toBe("11% to go to reach your goal on Fractions.");
  });

  it("uses the tiered pass titles and leaves a single miss as keep going", () => {
    expect(attemptBadge("passed", 2, 100)).toBe("💎 Diamond");
    expect(attemptTitle(2, 100)).toMatchObject({ label: "Diamond", color: "#0e7490" });
    expect(attemptTitle(3, 100)).toMatchObject({ label: "Tenacious", color: "#7c3aed" });
    expect(attemptTitle(4, 90)).toMatchObject({ label: "Breakthrough", color: "#b45309" });
    expect(attemptBadge("keep_going", 1, 40)).toBe("Keep going");
    expect(attemptBadge("not_attempted", 0, null)).toBe("Not attempted");
  });

  it("uses the needs-attention names and the shared pass mark", () => {
    expect(PASS_PERCENT).toBe(70);
    expect(topicsNeedingAttention({ topics_needing_attention: ["Algebra"] })).toEqual(["Algebra"]);
    expect(topicsNeedingAttention({ topics_needing_attention: [] })).toEqual([]);
    expect(topicsNeedingAttention({})).toBeNull();
    const fractions = (score: number | null) => ({
      title: "Fractions",
      overall_quiz: score === null ? null : { best_score_percent: score },
    });
    expect(weakTopicNames([fractions(70)])).toEqual([]);
    expect(weakTopicNames([fractions(69.99)])).toEqual(["Fractions"]);
    expect(
      weakTopicNames([
        fractions(null),
        { title: "Decimals", overall_quiz: { best_score_percent: null } },
      ]),
    ).toEqual([]);
  });

  it("averages the gain from the first attempt to the latest", () => {
    expect(
      momentumGain([
        [
          { attempt_number: 1, score_percent: "40" },
          { attempt_number: 4, score_percent: "90" },
        ],
        [{ attempt_number: 1, score_percent: "10" }],
      ]),
    ).toBe(50);
    expect(momentumGain([[{ attempt_number: 1, score_percent: "10" }]])).toBeNull();
  });
});
