import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { GenerationStepDetail } from "../api/types";

vi.mock("../api/generation", () => ({
  getGenerationStepDetail: vi.fn(),
}));

import { getGenerationStepDetail } from "../api/generation";
import { GenerationStepDetailPanel } from "./GenerationStepDetail";

const timing: GenerationStepDetail["timing"] = {
  step: "lesson",
  label: "Generating lesson",
  status: "complete",
  started_at: "2026-10-01T00:00:00Z",
  ended_at: "2026-10-01T00:02:00Z",
  duration_seconds: 120,
  error: null,
  available: true,
  hint: null,
};

function detail(step: "lesson" | "items"): GenerationStepDetail {
  return {
    timing: { ...timing, step, label: step === "lesson" ? "Generating lesson" : "Generating quiz items" },
    indexing: null,
    outline: null,
    outline_review: null,
    lesson:
      step === "lesson"
        ? {
            markdown: "# Area\n\nThe area of a rectangle is length times width.",
            published_lesson_version_id: null,
            needs_review: true,
            judge: [
              {
                subject_kind: "lesson_section",
                subject_key: "section-1",
                label: "Area",
                decision: "needs_review",
                reasons: ["Coverage of 'Find the area.' is partial (score 1.20)."],
                checks: [
                  {
                    check_id: "grounded",
                    kind: "noul",
                    decision: "accept",
                    summary: "0.91",
                    probabilities: { true: 0.91, false: 0.09 },
                  },
                  {
                    check_id: "coverage_01",
                    kind: "score",
                    decision: "needs_review",
                    summary: "partial (1.20)",
                    probabilities: { "0": 0.1, "1": 0.6, "2": 0.2, "3": 0.1 },
                  },
                ],
                model: "typesafe/jev-1.14",
              },
            ],
          }
        : null,
    items:
      step === "items"
        ? {
            count: 1,
            difficulty_mix: { easy: 1, medium: 0, hard: 0, unlabeled: 0 },
            bloom_mix: { remember: 1, understand: 0, apply: 0, analyze: 0, unlabeled: 0 },
            subtopic_counts: [],
            passed: 1,
            fixed_on_retry: 0,
            dropped: 0,
            key_disputed: 0,
            items: [
              {
                question_id: "q1",
                question_version_id: "v1",
                prompt: "Which shape has four right angles?",
                options: { A: "Rectangle", B: "Circle", C: "Line", D: "Point" },
                correct_label: "A",
                correct_rationale: "A rectangle has four right angles.",
                distractor_rationales: {},
                subtopic_id: "sub",
                sequence: 1,
                judge_needs_review: true,
              },
            ],
            judge: [
              {
                subject_kind: "quiz_item",
                subject_key: "v1",
                question_version_id: "v1",
                label: "Which shape has four right angles?",
                decision: "reject",
                reasons: ["Keyed answer is correct: noul 0.22 is at or below 0.35."],
                checks: [
                  {
                    check_id: "keyed_correct",
                    kind: "noul",
                    decision: "reject",
                    summary: "0.22",
                    probabilities: { true: 0.22, false: 0.78 },
                  },
                ],
                model: "typesafe/jev-1.14",
              },
            ],
          }
        : null,
    ready: null,
  };
}

describe("GenerationStepDetail Jev scores", () => {
  it("shows lesson probabilities and the decision without an action button", async () => {
    vi.mocked(getGenerationStepDetail).mockResolvedValue(detail("lesson"));
    render(
      <GenerationStepDetailPanel runId="run-1" step="lesson" onClose={() => undefined} />,
    );
    expect(await screen.findByRole("heading", { name: "Jev scores" })).toBeInTheDocument();
    expect(screen.getByText("Needs review before publish")).toBeInTheDocument();
    expect(screen.getAllByText("Needs review").length).toBeGreaterThan(0);
    expect(screen.getByText(/grounded: 0.91/)).toBeInTheDocument();
    expect(screen.getByText(/true 0.91/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /accept/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /reject/i })).not.toBeInTheDocument();
  });

  it("shows quiz-item decisions on the items step", async () => {
    vi.mocked(getGenerationStepDetail).mockResolvedValue(detail("items"));
    render(<GenerationStepDetailPanel runId="run-1" step="items" onClose={() => undefined} />);
    expect((await screen.findAllByText("Reject")).length).toBeGreaterThan(0);
    expect(screen.getByText("Needs review")).toBeInTheDocument();
    expect(screen.getByText(/keyed_correct: 0.22/)).toBeInTheDocument();
    expect(screen.getByText(/noul 0.22 is at or below 0.35/)).toBeInTheDocument();
  });
});
