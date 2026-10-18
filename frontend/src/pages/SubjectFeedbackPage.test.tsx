import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SubjectFeedbackPage } from "./SubjectFeedbackPage";

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Asha Student" } }),
}));

vi.mock("../api/feedback", () => ({
  getSubjectFeedback: vi.fn(),
  setSubtopicGoal: vi.fn(),
  deleteSubtopicGoal: vi.fn(),
}));

vi.mock("../api/engagement", () => ({
  getSubjectEngagementRates: vi.fn(),
  getEngagementRate: vi.fn(),
}));

vi.mock("../api/selfEvaluation", () => ({
  getSelfEvaluation: vi.fn(),
  recordSelfEvaluation: vi.fn(),
}));

import { getSubjectEngagementRates, getEngagementRate } from "../api/engagement";
import { getSubjectFeedback } from "../api/feedback";
import { getSelfEvaluation } from "../api/selfEvaluation";

const dashboard = {
  subject_id: "math-1",
  subject_name: "Mathematics",
  overall_percent: "80",
  attempt_count: 1,
  strength_subtopic_id: "st-1",
  top_focus_subtopic_ids: [],
  regressions: [],
  summary: "Template summary.",
  subtopics: [
    {
      subtopic_id: "st-1",
      subtopic_name: "Square numbers",
      topic_name: "Number patterns",
      percent: "80",
      question_count: 4,
      attempts: [{ sequence: 1, percent: "80", submitted_at: "2026-10-01T00:00:00Z" }],
      prior_percent: null,
      delta: null,
      percent_easy: null,
      easy_question_count: 0,
      percent_medium: null,
      medium_question_count: 0,
      percent_hard: null,
      hard_question_count: 0,
      class_avg_percent: null,
      class_sample_size: 0,
      is_weak: false,
      remediation: null,
      goal: null,
    },
  ],
};

describe("SubjectFeedbackPage", () => {
  beforeEach(() => {
    vi.mocked(getSubjectFeedback).mockReset();
    vi.mocked(getSubjectEngagementRates).mockReset();
    vi.mocked(getEngagementRate).mockReset();
    vi.mocked(getSelfEvaluation).mockReset();
    vi.mocked(getSubjectFeedback).mockResolvedValue(dashboard);
    vi.mocked(getSubjectEngagementRates).mockResolvedValue([
      {
        subtopic_id: "st-1",
        engaged_slides: 1,
        total_slides: 2,
        engagement_rate: 0.5,
      },
    ]);
    vi.mocked(getSelfEvaluation).mockResolvedValue({
      subject_id: "math-1",
      confidence: "getting_it",
    });
  });

  it("loads scores, engagement, and the self-check together", async () => {
    render(
      <MemoryRouter initialEntries={["/subjects/math-1/feedback"]}>
        <Routes>
          <Route path="/subjects/:subjectId/feedback" element={<SubjectFeedbackPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "Your personalized feedback" })).toBeInTheDocument();
    expect(getSubjectFeedback).toHaveBeenCalledWith("math-1");
    expect(getSubjectEngagementRates).toHaveBeenCalledWith("math-1");
    expect(getSelfEvaluation).toHaveBeenCalledWith("math-1");
    expect(getEngagementRate).not.toHaveBeenCalled();
    expect(await screen.findByText(/50% engaged/)).toBeInTheDocument();
  });
});
