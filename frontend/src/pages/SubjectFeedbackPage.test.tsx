import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FeedbackSubjectPickerPage } from "./FeedbackSubjectPickerPage";
import { SubjectFeedbackPage } from "./SubjectFeedbackPage";
import { TopicFeedbackPage } from "./TopicFeedbackPage";

vi.mock("../api/feedback", () => ({
  getFeedbackOverview: vi.fn(),
  getFeedbackSubject: vi.fn(),
  getFeedbackTopic: vi.fn(),
  getFeedbackHighlights: vi.fn(),
  getSubjectFeedback: vi.fn(),
  setSubtopicGoal: vi.fn(),
  deleteSubtopicGoal: vi.fn(),
}));

vi.mock("../api/materials", () => ({
  fetchLearningDirectory: vi.fn(),
}));

vi.mock("../api/selfEvaluation", () => ({
  getSelfEvaluation: vi.fn(),
  recordSelfEvaluation: vi.fn(),
}));

vi.mock("../api/engagement", () => ({
  getSubjectEngagementRates: vi.fn(),
}));

import {
  deleteSubtopicGoal,
  getFeedbackHighlights,
  getFeedbackOverview,
  getFeedbackSubject,
  getFeedbackTopic,
  setSubtopicGoal,
} from "../api/feedback";
import { getSubjectEngagementRates } from "../api/engagement";
import { fetchLearningDirectory } from "../api/materials";
import { getSelfEvaluation, recordSelfEvaluation } from "../api/selfEvaluation";

const overview = {
  score_percent: "75",
  subjects: [
    {
      subject_id: "math-1",
      subject_name: "Mathematics",
      grade_name: "Grade 8",
      score_percent: "80",
      quizzes_taken: 4,
      trend: "improving" as const,
      trend_delta: "20",
      strongest_topic: {
        topic_id: "mensuration",
        topic_name: "Mensuration",
        best_score_percent: "90",
      },
      weakest_topic: {
        topic_id: "algebra",
        topic_name: "Algebra",
        best_score_percent: "40",
      },
      concepts_mastered: 2,
      concepts_attempted: 4,
      focus: { concept_name: "Algebra", topic_id: "algebra", gap_percent: 30 },
      achievement: null,
    },
    {
      subject_id: "sci-1",
      subject_name: "Science",
      grade_name: "Grade 8",
      score_percent: null,
      quizzes_taken: 0,
      trend: null,
      trend_delta: null,
      strongest_topic: null,
      weakest_topic: null,
      concepts_mastered: 0,
      concepts_attempted: 0,
      focus: null,
      achievement: null,
    },
  ],
};

const subject = {
  subject_id: "math-1",
  subject_name: "Mathematics",
  grade_name: "Grade 8",
  score_percent: "90",
  quizzes_taken: 4,
  trend: "improving" as const,
  trend_delta: "20",
  topics_needing_attention: ["Algebra"],
  summary: "You're doing well in Mensuration.",
  summary_status: "fallback" as const,
  concepts_mastered: 1,
  concepts_attempted: 2,
  focus: { concept_name: "Area of a polygon", topic_id: "mensuration", gap_percent: 45 },
  achievement: null,
  topics: [
    {
      topic_id: "mensuration",
      topic_name: "Mensuration",
      sequence: 1,
      best_score_percent: "90",
      latest_score_percent: "90",
      attempt_count: 4,
      trend: "improving" as const,
      trend_delta: "20",
      badge: "passed" as const,
      quiz_id: "quiz-1",
      has_lesson: true,
    },
    {
      topic_id: "decimals",
      topic_name: "Decimals",
      sequence: 2,
      best_score_percent: null,
      latest_score_percent: null,
      attempt_count: 0,
      trend: null,
      trend_delta: null,
      badge: "not_attempted" as const,
      quiz_id: "quiz-2",
      has_lesson: true,
    },
  ],
};

const topic = {
  subject_id: "math-1",
  subject_name: "Mathematics",
  topic_id: "mensuration",
  topic_name: "Mensuration",
  best_score_percent: "90",
  latest_score_percent: "90",
  attempt_count: 4,
  trend: "improving" as const,
  trend_delta: "20",
  badge: "passed" as const,
  quiz_id: "quiz-1",
  has_lesson: true,
  summary: "Strong on Cuboid.",
  concepts_mastered: 1,
  concepts_attempted: 2,
  subtopics: [
    {
      subtopic_id: "cuboid-1",
      name: "Cuboid",
      correct_count: 3,
      answered_count: 4,
      percent: "75",
      band: "strong" as const,
      goal: null,
    },
    {
      subtopic_id: "polygon-1",
      name: "Area of a polygon",
      correct_count: 1,
      answered_count: 4,
      percent: "25",
      band: "needs_work" as const,
      goal: null,
    },
  ],
};

describe("feedback hierarchy", () => {
  beforeEach(() => {
    vi.mocked(getFeedbackOverview).mockReset();
    vi.mocked(getFeedbackSubject).mockReset();
    vi.mocked(getFeedbackTopic).mockReset();
    vi.mocked(getSelfEvaluation).mockReset();
    vi.mocked(getFeedbackOverview).mockResolvedValue(overview);
    vi.mocked(getFeedbackSubject).mockResolvedValue(subject);
    vi.mocked(getFeedbackTopic).mockResolvedValue(topic);
    vi.mocked(getFeedbackHighlights).mockResolvedValue({ due_reviews: [], trajectories: [] });
    vi.mocked(fetchLearningDirectory).mockResolvedValue({ subjects: [] });
    vi.mocked(getSubjectEngagementRates).mockResolvedValue([]);
    vi.mocked(setSubtopicGoal).mockReset();
    vi.mocked(deleteSubtopicGoal).mockReset();
    vi.mocked(recordSelfEvaluation).mockResolvedValue({
      subject_id: "math-1",
      confidence: "confident",
    });
    vi.mocked(getSelfEvaluation).mockResolvedValue({
      subject_id: "math-1",
      confidence: null,
    });
  });

  it("shows one card per subject and an overall score", async () => {
    render(
      <MemoryRouter>
        <FeedbackSubjectPickerPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("region", { name: "Across all subjects" })).toHaveTextContent("75%");
    expect(screen.getByRole("heading", { name: "Mathematics" })).toBeInTheDocument();
    expect(screen.getByText(/Strongest topic: Mensuration/)).toBeInTheDocument();
    expect(screen.getByText(/Weakest topic: Algebra/)).toBeInTheDocument();
    expect(screen.getByText("Up 20 points since the previous attempt")).toBeInTheDocument();
    const science = screen.getByRole("link", { name: /Science/ });
    expect(science).toHaveTextContent("Not attempted");
    expect(science).not.toHaveTextContent("0%");
    expect(science).not.toHaveTextContent("declining");
    expect(screen.queryByText(/· -/)).not.toBeInTheDocument();
    expect(screen.getAllByText("View feedback").length).toBeGreaterThan(0);
    expect(screen.getByText(/You're just 30% from your goal on Algebra/)).toBeInTheDocument();
  });

  it("lists topics in order and leaves an unattempted topic blank", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/subjects/math-1/feedback"]}>
        <Routes>
          <Route path="/subjects/:subjectId/feedback" element={<SubjectFeedbackPage />} />
          <Route
            path="/subjects/:subjectId/feedback/topics/:topicId"
            element={<TopicFeedbackPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "Mathematics" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Subject summary" })).toHaveTextContent(
      "You're doing well in Mensuration.",
    );
    expect(screen.getByText("Needs attention: Algebra")).toBeInTheDocument();
    const links = screen.getAllByRole("link");
    const topicLinks = links.filter((link) => link.getAttribute("href")?.includes("/topics/"));
    expect(topicLinks.map((link) => link.textContent)).toEqual([
      expect.stringContaining("Mensuration"),
      expect.stringContaining("Decimals"),
    ]);
    expect(screen.getByRole("link", { name: /Decimals/ })).toHaveTextContent("Not attempted");
    expect(screen.getByRole("link", { name: /Decimals/ })).not.toHaveTextContent("declining");
    await user.click(screen.getByRole("link", { name: /Mensuration/ }));
    expect(await screen.findByRole("heading", { name: "Mensuration" })).toBeInTheDocument();
  });

  it("shows cleaned concept names and links to the lesson and quiz", async () => {
    render(
      <MemoryRouter initialEntries={["/subjects/math-1/feedback/topics/mensuration"]}>
        <Routes>
          <Route
            path="/subjects/:subjectId/feedback/topics/:topicId"
            element={<TopicFeedbackPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "Mensuration" })).toBeInTheDocument();
    expect(screen.getByText("Cuboid")).toBeInTheDocument();
    expect(screen.getByText("Area of a polygon")).toBeInTheDocument();
    expect(screen.queryByText(/9\.4\.1/)).not.toBeInTheDocument();
    expect(screen.queryByText(/EXERCISE/)).not.toBeInTheDocument();
    expect(screen.getByText("Strong")).toBeInTheDocument();
    expect(screen.getByText("Needs work")).toBeInTheDocument();
    expect(screen.getByText("Strong on Cuboid.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review the lesson" })).toHaveAttribute(
      "href",
      "/subjects/math-1/topics/mensuration/lesson",
    );
    expect(screen.getByRole("link", { name: "Retake the quiz" })).toHaveAttribute(
      "href",
      "/quizzes/quiz-1",
    );
    expect(getFeedbackTopic).toHaveBeenCalledWith("mensuration");
    expect(screen.getByText("💡 Breakthrough")).toBeInTheDocument();
    expect(screen.getByText("1 of 2 concepts mastered")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Set a goal" })).toHaveLength(2);
  });

  it("sets and removes a concept goal on the topic page", async () => {
    const user = userEvent.setup();
    vi.mocked(setSubtopicGoal).mockResolvedValue({
      subtopic_id: "cuboid-1",
      subtopic_name: "Cuboid",
      target_percent: "80",
      due_at: "2026-12-01T23:59:59.000Z",
      current_percent: "75",
      status: "on_track",
    });
    vi.mocked(deleteSubtopicGoal).mockResolvedValue(undefined);
    render(
      <MemoryRouter initialEntries={["/subjects/math-1/feedback/topics/mensuration"]}>
        <Routes>
          <Route
            path="/subjects/:subjectId/feedback/topics/:topicId"
            element={<TopicFeedbackPage />}
          />
        </Routes>
      </MemoryRouter>,
    );

    const setButtons = await screen.findAllByRole("button", { name: "Set a goal" });
    await user.click(setButtons[0]);
    await user.clear(screen.getByLabelText("Target percent for Cuboid"));
    await user.type(screen.getByLabelText("Target percent for Cuboid"), "80");
    await user.type(screen.getByLabelText("Due date for Cuboid"), "2026-12-01");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("On track")).toBeInTheDocument();
    expect(screen.getByText(/Goal: 80% by/)).toBeInTheDocument();
    expect(setSubtopicGoal).toHaveBeenCalledWith("cuboid-1", {
      target_percent: 80,
      due_at: "2026-12-01T23:59:59.000Z",
    });

    await user.click(screen.getByRole("button", { name: "Remove goal" }));
    expect(await screen.findAllByRole("button", { name: "Set a goal" })).toHaveLength(2);
    expect(deleteSubtopicGoal).toHaveBeenCalledWith("cuboid-1");
  });

  it("shows the engagement badge and the confidence note on the subject page", async () => {
    const user = userEvent.setup();
    vi.mocked(getSubjectEngagementRates).mockResolvedValue([
      { subtopic_id: "cuboid-1", engaged_slides: 1, total_slides: 2, engagement_rate: 0.5 },
    ]);
    render(
      <MemoryRouter initialEntries={["/subjects/math-1/feedback"]}>
        <Routes>
          <Route path="/subjects/:subjectId/feedback" element={<SubjectFeedbackPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText(/50% engaged · Skimmed/)).toBeInTheDocument();
    expect(screen.getByText("💡 Breakthrough")).toBeInTheDocument();
    expect(screen.getAllByText("Not attempted").length).toBeGreaterThan(0);
    expect(screen.getByText(/You're just 45% from your goal on Area of a polygon/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View report card" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confident" }));
    expect(
      await screen.findByText("Confident and scoring high — you really do know this. Nice."),
    ).toBeInTheDocument();
    expect(recordSelfEvaluation).toHaveBeenCalledWith("math-1", "confident");
  });
});
