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
  getSubjectFeedback: vi.fn(),
  setSubtopicGoal: vi.fn(),
  deleteSubtopicGoal: vi.fn(),
}));

vi.mock("../api/selfEvaluation", () => ({
  getSelfEvaluation: vi.fn(),
  recordSelfEvaluation: vi.fn(),
}));

import { getFeedbackOverview, getFeedbackSubject, getFeedbackTopic } from "../api/feedback";
import { getSelfEvaluation } from "../api/selfEvaluation";

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
  subtopics: [
    {
      name: "Cuboid",
      correct_count: 3,
      answered_count: 4,
      percent: "75",
      band: "strong" as const,
    },
    {
      name: "Area of a polygon",
      correct_count: 1,
      answered_count: 4,
      percent: "25",
      band: "needs_work" as const,
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
  });
});
