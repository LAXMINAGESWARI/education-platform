import { describe, expect, it } from "vitest";

import { latestRetakeLabel, passedBestLabel, quizActionLabel, trackedAttempts } from "./quizAction";

describe("quizActionLabel", () => {
  it("starts a fresh quiz when nothing has been submitted", () => {
    expect(quizActionLabel(null)).toBe("Start quiz");
    expect(
      quizActionLabel({
        recent_attempts: [{ status: "in_progress" }],
      } as never),
    ).toBe("Start quiz");
    expect(
      quizActionLabel({
        recent_attempts: [{ status: "abandoned" }],
      } as never),
    ).toBe("Start quiz");
  });

  it("retakes after a finished attempt", () => {
    expect(
      quizActionLabel({
        recent_attempts: [{ status: "scored" }],
      } as never),
    ).toBe("Retake quiz");
  });
});

describe("pass and latest labels", () => {
  const passedThenFailed = {
    passed: true,
    best_score_percent: 80,
    recent_attempts: [
      {
        id: "latest",
        status: "released",
        passed: false,
        score_percent: 20,
        submitted_at: "2026-10-17T09:00:00Z",
      },
      {
        id: "best",
        status: "released",
        passed: true,
        score_percent: 80,
        submitted_at: "2026-10-01T09:00:00Z",
      },
    ],
  };

  it("shows the best pass and a later failure on its own line", () => {
    expect(passedBestLabel(passedThenFailed as never)).toBe("Passed · best 80%");
    expect(latestRetakeLabel(passedThenFailed as never)).toBe("Latest retake 17 Oct · 20%");
  });

  it("hides the retake line when nothing has passed", () => {
    expect(passedBestLabel({ passed: false, recent_attempts: [] } as never)).toBeNull();
    expect(latestRetakeLabel({ passed: false, recent_attempts: [] } as never)).toBeNull();
  });
});

describe("trackedAttempts", () => {
  it("drops abandoned attempts so they are not kept in history", () => {
    expect(
      trackedAttempts([
        { id: "a", status: "abandoned" },
        { id: "b", status: "scored" },
        { id: "c", status: "in_progress" },
      ] as never),
    ).toEqual([
      { id: "b", status: "scored" },
      { id: "c", status: "in_progress" },
    ]);
  });
});
