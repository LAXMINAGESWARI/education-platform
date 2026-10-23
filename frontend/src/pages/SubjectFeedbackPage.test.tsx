import { act, render, screen, waitFor, within } from "@testing-library/react";
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
  setTopicGoal: vi.fn(),
  deleteTopicGoal: vi.fn(),
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

vi.mock("../api/attempts", () => ({
  listQuizAttempts: vi.fn(),
}));

import { listQuizAttempts } from "../api/attempts";
import { HASH_FOCUS_CLASS } from "../lib/hashFocus";
import {
  deleteTopicGoal,
  getFeedbackHighlights,
  getFeedbackOverview,
  getFeedbackSubject,
  getFeedbackTopic,
  setTopicGoal,
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
      focus: { topic_name: "Algebra", topic_id: "algebra", gap_percent: 8 },
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
  focus: { topic_name: "Mensuration", topic_id: "mensuration", gap_percent: 45 },
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
      goal: {
        topic_id: "mensuration",
        topic_name: "Mensuration",
        target_percent: "80",
        due_at: "2026-12-01T23:59:59.000Z",
        current_percent: "90",
        status: "achieved" as const,
      },
      attempt_scores: [
        { attempt_number: 1, score_percent: "40" },
        { attempt_number: 2, score_percent: "55" },
        { attempt_number: 3, score_percent: "70" },
        { attempt_number: 4, score_percent: "90" },
      ],
      concepts: [
        {
          subtopic_id: "polygon-1",
          name: "Area of a polygon",
          correct_count: 1,
          answered_count: 4,
          percent: "25",
          band: "needs_work" as const,
        },
        {
          subtopic_id: "cuboid-1",
          name: "Cuboid",
          correct_count: 3,
          answered_count: 4,
          percent: "75",
          band: "strong" as const,
        },
      ],
      regression_count: 2,
      class_avg_percent: "62",
      class_sample_size: 8,
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
      goal: null,
      attempt_scores: [],
      concepts: [],
      regression_count: null,
      class_avg_percent: null,
      class_sample_size: 0,
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
  goal: null,
  average_score_percent: "63.75",
  attempt_scores: [
    { attempt_number: 1, score_percent: "40" },
    { attempt_number: 2, score_percent: "55" },
    { attempt_number: 3, score_percent: "70" },
    { attempt_number: 4, score_percent: "90" },
  ],
  questions_answered: 8,
  questions_correct: 4,
  accuracy_percent: "50",
  time_spent_seconds: 600,
  difficulty_accuracy: [
    { difficulty: "easy" as const, correct_count: 2, answered_count: 2, percent: "100" },
    { difficulty: "hard" as const, correct_count: 0, answered_count: 1, percent: "0" },
  ],
  subtopics: [
    {
      subtopic_id: "cuboid-1",
      name: "Cuboid",
      correct_count: 3,
      answered_count: 4,
      percent: "75",
      band: "strong" as const,
    },
    {
      subtopic_id: "polygon-1",
      name: "Area of a polygon",
      correct_count: 1,
      answered_count: 4,
      percent: "25",
      band: "needs_work" as const,
    },
  ],
};

const pastAttempts = [
  {
    id: "attempt-1",
    attempt_number: 1,
    status: "submitted",
    score_percent: "40",
    passed: false,
    started_at: "2026-08-01T10:00:00Z",
    submitted_at: "2026-08-01T10:20:00Z",
  },
  {
    id: "attempt-4",
    attempt_number: 4,
    status: "submitted",
    score_percent: "90",
    passed: true,
    started_at: "2026-08-04T10:00:00Z",
    submitted_at: "2026-08-04T10:20:00Z",
  },
];

describe("feedback hierarchy", () => {
  beforeEach(() => {
    vi.mocked(getFeedbackOverview).mockReset();
    vi.mocked(listQuizAttempts).mockReset();
    vi.mocked(listQuizAttempts).mockResolvedValue(pastAttempts);
    vi.mocked(getFeedbackSubject).mockReset();
    vi.mocked(getFeedbackTopic).mockReset();
    vi.mocked(getSelfEvaluation).mockReset();
    vi.mocked(getFeedbackOverview).mockResolvedValue(overview);
    vi.mocked(getFeedbackSubject).mockResolvedValue(subject);
    vi.mocked(getFeedbackTopic).mockResolvedValue(topic);
    vi.mocked(getFeedbackHighlights).mockResolvedValue({ due_reviews: [], trajectories: [] });
    vi.mocked(fetchLearningDirectory).mockResolvedValue({ subjects: [] });
    vi.mocked(getSubjectEngagementRates).mockResolvedValue([]);
    vi.mocked(setTopicGoal).mockReset();
    vi.mocked(deleteTopicGoal).mockReset();
    vi.mocked(recordSelfEvaluation).mockResolvedValue({
      subject_id: "math-1",
      confidence: "confident",
    });
    vi.mocked(getSelfEvaluation).mockResolvedValue({
      subject_id: "math-1",
      confidence: null,
    });
  });

  it("shows one row per subject and an overall score", async () => {
    render(
      <MemoryRouter>
        <FeedbackSubjectPickerPage />
      </MemoryRouter>,
    );

    const summary = await screen.findByRole("region", { name: "Across all subjects" });
    expect(summary).toHaveTextContent("75%");
    expect(summary).toHaveTextContent("2 mastered out of 4 you've tried");
    expect(summary).not.toHaveTextContent("Concepts you have already tried");
    const table = screen.getByRole("table", { name: "Feedback by subject" });
    const math = within(table).getByRole("row", { name: /Mathematics/ });
    expect(math).toHaveTextContent("Strongest topic: Mensuration");
    expect(math).toHaveTextContent("Weakest topic: Algebra");
    expect(math).toHaveTextContent("Up 20 points since the previous attempt");
    expect(math).toHaveTextContent("You're just 8% from your goal on Algebra.");
    expect(within(math).getByText("Mathematics")).toHaveClass("badge", "badge--info");
    const science = within(table).getByRole("row", { name: /Science/ });
    expect(science).toHaveTextContent("Not attempted");
    expect(science).not.toHaveTextContent("0%");
    expect(science).not.toHaveTextContent("declining");
    expect(screen.queryByText(/· -/)).not.toBeInTheDocument();
    expect(within(table).getAllByRole("link", { name: "View feedback" })).toHaveLength(2);
    expect(within(science).getByRole("link", { name: "View feedback" })).toHaveAttribute(
      "href",
      "/subjects/sci-1/feedback",
    );
  });

  it("filters due reviews to the subject in the query", async () => {
    vi.mocked(getFeedbackHighlights).mockResolvedValue({
      due_reviews: [
        {
          subtopic_id: "st-math",
          subtopic_name: "Fractions practice",
          topic_name: "Fractions",
          days_since_last_attempt: 10,
          due_interval_days: 7,
          remediation: null,
        },
        {
          subtopic_id: "st-sci",
          subtopic_name: "Forces practice",
          topic_name: "Forces",
          days_since_last_attempt: 9,
          due_interval_days: 7,
          remediation: null,
        },
      ],
      trajectories: [],
    });
    vi.mocked(fetchLearningDirectory).mockResolvedValue({
      subjects: [
        {
          id: "math-1",
          code: "MATH",
          name: "Mathematics",
          grade_name: "Grade 6",
          academic_period_name: "Term 1",
          progress_percent: 0,
          topics: [
            {
              id: "topic-1",
              title: "Fractions",
              slug: "fractions",
              sequence: 1,
              progress_percent: 0,
              complete: false,
              objectives: [],
              overall_quiz: null,
              subtopics: [
                {
                  id: "st-math",
                  title: "Fractions practice",
                  slug: "fractions-practice",
                  sequence: 1,
                  has_lesson: false,
                  lesson_completed: false,
                  progress_percent: 0,
                  quiz: null,
                },
              ],
            },
          ],
        },
        {
          id: "sci-1",
          code: "SCI",
          name: "Science",
          grade_name: "Grade 6",
          academic_period_name: "Term 1",
          progress_percent: 0,
          topics: [
            {
              id: "topic-2",
              title: "Forces",
              slug: "forces",
              sequence: 1,
              progress_percent: 0,
              complete: false,
              objectives: [],
              overall_quiz: null,
              subtopics: [
                {
                  id: "st-sci",
                  title: "Forces practice",
                  slug: "forces-practice",
                  sequence: 1,
                  has_lesson: false,
                  lesson_completed: false,
                  progress_percent: 0,
                  quiz: null,
                },
              ],
            },
          ],
        },
      ],
    });

    render(
      <MemoryRouter initialEntries={["/feedback?subject=math-1&section=due"]}>
        <FeedbackSubjectPickerPage />
      </MemoryRouter>,
    );

    const due = await screen.findByRole("region", { name: "Due for review" });
    expect(due).toHaveAttribute("id", "feedback-due");
    expect(due).toHaveTextContent("1 subtopic is past the review interval.");
    expect(due).toHaveTextContent("Fractions practice");
    expect(due).not.toHaveTextContent("Forces practice");
  });

  it("scrolls to due reviews and focuses the heading after feedback loads", async () => {
    const previous = HTMLElement.prototype.scrollIntoView;
    const scrollIntoView = vi.fn();
    HTMLElement.prototype.scrollIntoView = scrollIntoView;
    try {
      render(
        <MemoryRouter initialEntries={["/feedback?section=due#feedback-due"]}>
          <FeedbackSubjectPickerPage />
        </MemoryRouter>,
      );

      const heading = await screen.findByRole("heading", { name: "Due for review" });
      await waitFor(() => {
        expect(heading).toHaveFocus();
      });
      expect(scrollIntoView).toHaveBeenCalledWith({ block: "start" });
      expect(heading).toHaveClass(HASH_FOCUS_CLASS);
    } finally {
      HTMLElement.prototype.scrollIntoView = previous;
    }
  });

  it("scrolls due reviews to the start only after due rows render", async () => {
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      frames.push(callback);
      return frames.length;
    });
    vi.stubGlobal("cancelAnimationFrame", () => undefined);
    let releaseHighlights: (value: Awaited<ReturnType<typeof getFeedbackHighlights>>) => void = () => undefined;
    vi.mocked(getFeedbackHighlights).mockReturnValue(
      new Promise((resolve) => {
        releaseHighlights = resolve;
      }),
    );
    vi.mocked(fetchLearningDirectory).mockResolvedValue({
      subjects: [
        {
          id: "math-1",
          code: "MATH",
          name: "Mathematics",
          grade_name: "Grade 6",
          academic_period_name: "Term 1",
          progress_percent: 0,
          topics: [
            {
              id: "topic-1",
              title: "Fractions",
              slug: "fractions",
              sequence: 1,
              progress_percent: 0,
              complete: false,
              objectives: [],
              overall_quiz: null,
              subtopics: [
                {
                  id: "st-math",
                  title: "Fractions practice",
                  slug: "fractions-practice",
                  sequence: 1,
                  has_lesson: false,
                  lesson_completed: false,
                  progress_percent: 0,
                  quiz: null,
                },
              ],
            },
          ],
        },
      ],
    });
    const previous = HTMLElement.prototype.scrollIntoView;
    const scrollIntoView = vi.fn();
    HTMLElement.prototype.scrollIntoView = scrollIntoView;
    const flushFrames = () => {
      const pending = frames.splice(0);
      for (const callback of pending) callback(0);
    };
    try {
      render(
        <MemoryRouter initialEntries={["/feedback?subject=math-1&section=due#feedback-due"]}>
          <FeedbackSubjectPickerPage />
        </MemoryRouter>,
      );

      expect(await screen.findByRole("heading", { name: "Feedback by subject" })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Due for review" })).toBeInTheDocument();
      flushFrames();
      expect(scrollIntoView).not.toHaveBeenCalled();
      expect(screen.queryByText("Fractions practice")).not.toBeInTheDocument();

      releaseHighlights({
        due_reviews: [
          {
            subtopic_id: "st-math",
            subtopic_name: "Fractions practice",
            topic_name: "Fractions",
            days_since_last_attempt: 10,
            due_interval_days: 7,
            remediation: null,
          },
        ],
        trajectories: [],
      });

      expect(await screen.findByText("Fractions practice")).toBeInTheDocument();
      expect(scrollIntoView).not.toHaveBeenCalled();
      flushFrames();
      expect(scrollIntoView).not.toHaveBeenCalled();
      flushFrames();
      expect(scrollIntoView).toHaveBeenCalledTimes(1);
      expect(scrollIntoView).toHaveBeenCalledWith({ block: "start" });
      const heading = screen.getByRole("heading", { name: "Due for review" });
      expect(heading).toHaveClass(HASH_FOCUS_CLASS);
      expect(heading).toHaveFocus();
    } finally {
      HTMLElement.prototype.scrollIntoView = previous;
      vi.unstubAllGlobals();
    }
  });

  it("scrolls to needs attention and focuses that heading after subject feedback loads", async () => {
    const previous = HTMLElement.prototype.scrollIntoView;
    const scrollIntoView = vi.fn();
    HTMLElement.prototype.scrollIntoView = scrollIntoView;
    try {
      render(
        <MemoryRouter initialEntries={["/subjects/math-1/feedback#needs-attention"]}>
          <Routes>
            <Route path="/subjects/:subjectId/feedback" element={<SubjectFeedbackPage />} />
          </Routes>
        </MemoryRouter>,
      );

      const heading = await screen.findByRole("heading", { name: "Needs attention: Algebra" });
      await waitFor(() => {
        expect(heading).toHaveFocus();
      });
      expect(scrollIntoView).toHaveBeenCalledWith({ block: "start" });
      expect(heading).toHaveAttribute("tabindex", "-1");
      expect(heading).toHaveClass(HASH_FOCUS_CLASS);
    } finally {
      HTMLElement.prototype.scrollIntoView = previous;
    }
  });

  it("does not focus needs attention when that list is empty", async () => {
    vi.mocked(getFeedbackSubject).mockResolvedValue({
      ...subject,
      topics_needing_attention: [],
    });
    const previous = HTMLElement.prototype.scrollIntoView;
    const scrollIntoView = vi.fn();
    HTMLElement.prototype.scrollIntoView = scrollIntoView;
    try {
      render(
        <MemoryRouter initialEntries={["/subjects/math-1/feedback#needs-attention"]}>
          <Routes>
            <Route path="/subjects/:subjectId/feedback" element={<SubjectFeedbackPage />} />
          </Routes>
        </MemoryRouter>,
      );

      const heading = await screen.findByRole("heading", {
        name: "No topics need attention right now.",
      });
      expect(heading).toHaveClass("sr-only");
      expect(heading).not.toHaveFocus();
      expect(scrollIntoView).not.toHaveBeenCalled();
    } finally {
      HTMLElement.prototype.scrollIntoView = previous;
    }
  });

  it("scrolls needs attention to the start only after the subject data renders", async () => {
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      frames.push(callback);
      return frames.length;
    });
    vi.stubGlobal("cancelAnimationFrame", () => undefined);
    let releaseSubject: (value: Awaited<ReturnType<typeof getFeedbackSubject>>) => void = () => undefined;
    vi.mocked(getFeedbackSubject).mockReturnValue(
      new Promise((resolve) => {
        releaseSubject = resolve;
      }),
    );
    const previous = HTMLElement.prototype.scrollIntoView;
    const scrollIntoView = vi.fn();
    HTMLElement.prototype.scrollIntoView = scrollIntoView;
    const flushFrames = () => {
      const pending = frames.splice(0);
      for (const callback of pending) callback(0);
    };
    try {
      render(
        <MemoryRouter initialEntries={["/subjects/math-1/feedback#needs-attention"]}>
          <Routes>
            <Route path="/subjects/:subjectId/feedback" element={<SubjectFeedbackPage />} />
          </Routes>
        </MemoryRouter>,
      );

      expect(await screen.findByText("Loading feedback…")).toBeInTheDocument();
      flushFrames();
      expect(scrollIntoView).not.toHaveBeenCalled();

      releaseSubject(subject);
      const heading = await screen.findByRole("heading", { name: "Needs attention: Algebra" });
      expect(scrollIntoView).not.toHaveBeenCalled();
      flushFrames();
      expect(scrollIntoView).not.toHaveBeenCalled();
      flushFrames();
      expect(scrollIntoView).toHaveBeenCalledTimes(1);
      expect(scrollIntoView).toHaveBeenCalledWith({ block: "start" });
      expect(heading).toHaveClass(HASH_FOCUS_CLASS);
      expect(heading).toHaveFocus();
    } finally {
      HTMLElement.prototype.scrollIntoView = previous;
      vi.unstubAllGlobals();
    }
  });

  it("does not scroll or move focus on a feedback load without a hash or section", async () => {
    vi.mocked(getFeedbackHighlights).mockResolvedValue({
      due_reviews: [
        {
          subtopic_id: "st-math",
          subtopic_name: "Fractions practice",
          topic_name: "Fractions",
          days_since_last_attempt: 10,
          due_interval_days: 7,
          remediation: null,
        },
      ],
      trajectories: [],
    });
    vi.mocked(fetchLearningDirectory).mockResolvedValue({
      subjects: [
        {
          id: "math-1",
          code: "MATH",
          name: "Mathematics",
          grade_name: "Grade 6",
          academic_period_name: "Term 1",
          progress_percent: 0,
          topics: [
            {
              id: "topic-1",
              title: "Fractions",
              slug: "fractions",
              sequence: 1,
              progress_percent: 0,
              complete: false,
              objectives: [],
              overall_quiz: null,
              subtopics: [
                {
                  id: "st-math",
                  title: "Fractions practice",
                  slug: "fractions-practice",
                  sequence: 1,
                  has_lesson: false,
                  lesson_completed: false,
                  progress_percent: 0,
                  quiz: null,
                },
              ],
            },
          ],
        },
      ],
    });
    const previous = HTMLElement.prototype.scrollIntoView;
    const scrollIntoView = vi.fn();
    HTMLElement.prototype.scrollIntoView = scrollIntoView;
    try {
      const picker = render(
        <MemoryRouter initialEntries={["/feedback"]}>
          <FeedbackSubjectPickerPage />
        </MemoryRouter>,
      );

      const dueHeading = await screen.findByRole("heading", { name: "Due for review" });
      await act(async () => {});
      expect(scrollIntoView).not.toHaveBeenCalled();
      expect(dueHeading).not.toHaveFocus();
      expect(document.activeElement).toBe(document.body);
      picker.unmount();

      render(
        <MemoryRouter initialEntries={["/subjects/math-1/feedback"]}>
          <Routes>
            <Route path="/subjects/:subjectId/feedback" element={<SubjectFeedbackPage />} />
          </Routes>
        </MemoryRouter>,
      );
      const attention = await screen.findByRole("heading", { name: "Needs attention: Algebra" });
      await act(async () => {});
      expect(scrollIntoView).not.toHaveBeenCalled();
      expect(attention).not.toHaveFocus();
      expect(document.activeElement).toBe(document.body);
    } finally {
      HTMLElement.prototype.scrollIntoView = previous;
    }
  });

  it("focuses due reviews from the section query without a hash", async () => {
    const previous = HTMLElement.prototype.scrollIntoView;
    const scrollIntoView = vi.fn();
    HTMLElement.prototype.scrollIntoView = scrollIntoView;
    try {
      render(
        <MemoryRouter initialEntries={["/feedback?section=due"]}>
          <FeedbackSubjectPickerPage />
        </MemoryRouter>,
      );
      const heading = await screen.findByRole("heading", { name: "Due for review" });
      await waitFor(() => {
        expect(heading).toHaveFocus();
      });
      expect(scrollIntoView).toHaveBeenCalledWith({ block: "start" });
    } finally {
      HTMLElement.prototype.scrollIntoView = previous;
    }
  });

  it("does not move focus when Try again reloads feedback", async () => {
    const previous = HTMLElement.prototype.scrollIntoView;
    const scrollIntoView = vi.fn();
    HTMLElement.prototype.scrollIntoView = scrollIntoView;
    vi.mocked(getFeedbackOverview).mockRejectedValueOnce(new Error("offline"));
    try {
      render(
        <MemoryRouter initialEntries={["/feedback?section=due"]}>
          <FeedbackSubjectPickerPage />
        </MemoryRouter>,
      );
      const retry = await screen.findByRole("button", { name: "Try again" });
      retry.focus();
      const user = userEvent.setup();
      await user.click(retry);
      const heading = await screen.findByRole("heading", { name: "Due for review" });
      await act(async () => {});
      expect(scrollIntoView).not.toHaveBeenCalled();
      expect(heading).not.toHaveFocus();
    } finally {
      HTMLElement.prototype.scrollIntoView = previous;
    }
  });

  it("does not focus a hidden needs-attention heading", async () => {
    vi.mocked(getFeedbackSubject).mockResolvedValue({
      ...subject,
      topics_needing_attention: [],
    });
    const previous = HTMLElement.prototype.scrollIntoView;
    const scrollIntoView = vi.fn();
    HTMLElement.prototype.scrollIntoView = scrollIntoView;
    try {
      render(
        <MemoryRouter initialEntries={["/subjects/math-1/feedback#needs-attention"]}>
          <Routes>
            <Route path="/subjects/:subjectId/feedback" element={<SubjectFeedbackPage />} />
          </Routes>
        </MemoryRouter>,
      );
      const heading = await screen.findByRole("heading", { name: "No topics need attention right now." });
      await act(async () => {});
      expect(heading).toHaveClass("sr-only");
      expect(scrollIntoView).not.toHaveBeenCalled();
      expect(heading).not.toHaveFocus();
      expect(document.activeElement).toBe(document.body);
    } finally {
      HTMLElement.prototype.scrollIntoView = previous;
    }
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
    expect(screen.getByRole("heading", { name: "Needs attention: Algebra" })).toHaveAttribute(
      "id",
      "needs-attention",
    );
    const links = screen.getAllByRole("link");
    const topicLinks = links.filter((link) => link.getAttribute("href")?.includes("/topics/"));
    expect(topicLinks.map((link) => link.textContent)).toEqual(["Mensuration", "Decimals"]);
    const decimals = screen.getByRole("article", { name: "Decimals" });
    expect(decimals).toHaveTextContent("Not attempted");
    expect(decimals).not.toHaveTextContent("declining");
    expect(decimals).not.toHaveTextContent("0%");
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
    expect(screen.getByRole("heading", { name: "Score and metrics" })).toBeInTheDocument();
    expect(screen.getByText("Attempts")).toBeInTheDocument();
    expect(screen.getByText("Best score")).toBeInTheDocument();
    expect(screen.getByText("Latest score")).toBeInTheDocument();
    expect(screen.getByText("Average score")).toBeInTheDocument();
    expect(screen.getByText("64%")).toBeInTheDocument();
    expect(screen.getByText("Since the previous attempt")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Past attempts" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Performance over time" })).toBeInTheDocument();
    expect(
      screen.getByRole("img", {
        name: /Quiz scores across 2 attempts. Latest 90 percent, best 90 percent/,
      }),
    ).toBeInTheDocument();
    const history = screen.getByRole("region", { name: "Past attempts" });
    const rows = within(history).getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("Not passed · 40%");
    expect(rows[0]).not.toHaveTextContent("—");
    expect(rows[1]).toHaveTextContent("Passed · 90%");
    expect(within(rows[0]).getByRole("link", { name: "View" })).toHaveAttribute(
      "href",
      "/attempts/attempt-1",
    );
    expect(within(rows[1]).getByRole("link", { name: "View" })).toHaveAttribute(
      "href",
      "/attempts/attempt-4",
    );
    expect(screen.getByRole("link", { name: "Show full history" })).toHaveAttribute(
      "href",
      "/subjects/math-1/topics/mensuration/history",
    );
    expect(listQuizAttempts).toHaveBeenCalledWith("quiz-1");
    expect(screen.getByText("4 of 8 correct, 50%")).toBeInTheDocument();
    expect(screen.getByText("10 min")).toBeInTheDocument();
    expect(screen.getByText("Easy")).toBeInTheDocument();
    expect(screen.getByText("2 of 2 correct, 100%")).toBeInTheDocument();
    expect(screen.getByText("Hard")).toBeInTheDocument();
    expect(screen.getByText("0 of 1 correct, 0%")).toBeInTheDocument();
    expect(screen.queryByText("Medium")).not.toBeInTheDocument();
    expect(screen.getByText("Cuboid")).toBeInTheDocument();
    expect(screen.getByText("Area of a polygon")).toBeInTheDocument();
    expect(screen.getByText("1 of 4 correct, 25%")).toBeInTheDocument();
    expect(screen.getByText("3 of 4 correct, 75%")).toBeInTheDocument();
    const concepts = within(screen.getByRole("list", { name: "Concepts" })).getAllByRole("listitem");
    expect(concepts[0]).toHaveTextContent("Area of a polygon");
    expect(concepts[1]).toHaveTextContent("Cuboid");
    expect(screen.queryByText(/9\.4\.1/)).not.toBeInTheDocument();
    expect(screen.queryByText(/EXERCISE/)).not.toBeInTheDocument();
    expect(screen.getByText("Strong")).toBeInTheDocument();
    expect(screen.getByText("Needs work")).toBeInTheDocument();
    expect(
      within(screen.getByRole("list", { name: "Concepts" })).queryByRole("button", { name: "Set a goal" }),
    ).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Set a goal" })).toHaveLength(1);
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
  });

  it("sets and removes a topic goal on the topic quiz card", async () => {
    const user = userEvent.setup();
    vi.mocked(setTopicGoal).mockResolvedValue({
      topic_id: "mensuration",
      topic_name: "Mensuration",
      target_percent: "80",
      due_at: "2026-12-01T23:59:59.000Z",
      current_percent: "90",
      status: "achieved",
    });
    vi.mocked(deleteTopicGoal).mockResolvedValue(undefined);
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

    const ideas = await screen.findByRole("list", { name: "Concepts" });
    expect(within(ideas).queryByRole("button", { name: "Set a goal" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Set a goal" }));
    await user.clear(screen.getByLabelText("Target percent for Mensuration"));
    await user.type(screen.getByLabelText("Target percent for Mensuration"), "80");
    await user.type(screen.getByLabelText("Due date for Mensuration"), "2026-12-01");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Achieved ✓")).toBeInTheDocument();
    expect(screen.getByText(/Goal: 80% by/)).toBeInTheDocument();
    expect(within(ideas).queryByRole("button", { name: "Set a goal" })).not.toBeInTheDocument();
    expect(setTopicGoal).toHaveBeenCalledWith("mensuration", {
      target_percent: 80,
      due_at: "2026-12-01T23:59:59.000Z",
    });

    await user.click(screen.getByRole("button", { name: "Remove goal" }));
    expect(await screen.findByRole("button", { name: "Set a goal" })).toBeInTheDocument();
    expect(within(ideas).queryByRole("button")).not.toBeInTheDocument();
    expect(deleteTopicGoal).toHaveBeenCalledWith("mensuration");
  });

  it("leaves out topic metrics that have no stored data", async () => {
    vi.mocked(listQuizAttempts).mockResolvedValue([]);
    vi.mocked(getFeedbackTopic).mockResolvedValue({
      ...topic,
      attempt_count: 0,
      best_score_percent: null,
      latest_score_percent: null,
      trend: null,
      trend_delta: null,
      badge: "not_attempted",
      average_score_percent: null,
      attempt_scores: [],
      questions_answered: null,
      questions_correct: null,
      accuracy_percent: null,
      time_spent_seconds: null,
      difficulty_accuracy: [],
      subtopics: [],
      concepts_mastered: 0,
      concepts_attempted: 0,
    });
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

    expect(await screen.findByRole("heading", { name: "Score and metrics" })).toBeInTheDocument();
    expect(screen.getByText("No quiz attempts yet.")).toBeInTheDocument();
    expect(screen.queryByText("Average score")).not.toBeInTheDocument();
    expect(screen.queryByText("Time spent")).not.toBeInTheDocument();
    expect(
      await screen.findByText(
        "No scored attempts yet. Take the quiz to see performance over time.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Performance over time" })).not.toBeInTheDocument();
    expect(screen.getByText("No attempts recorded yet.")).toBeInTheDocument();
    expect(screen.queryByText("Accuracy by difficulty")).not.toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Concepts" })).not.toBeInTheDocument();
    expect(screen.getAllByText("Not attempted").length).toBeGreaterThan(0);
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
    expect(screen.getAllByText("💡 Breakthrough").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Not attempted").length).toBeGreaterThan(0);
    expect(screen.getByText("45% to go to reach your goal on Mensuration.")).toBeInTheDocument();
    const mensuration = screen.getByRole("article", { name: "Mensuration" });
    expect(mensuration).toHaveTextContent("↑ New best!");
    expect(mensuration).toHaveTextContent("Attempt 1");
    expect(mensuration).toHaveTextContent("Attempt 4");
    expect(mensuration).toHaveTextContent("+20");
    expect(mensuration).toHaveTextContent("Class average 62%");
    expect(mensuration).toHaveTextContent("2 questions you got right before are now wrong.");
    expect(mensuration).toHaveTextContent("1 strong · 1 needs work");
    expect(mensuration).toHaveTextContent("Achieved ✓");
    expect(mensuration.querySelector(".badge--title")).toHaveStyle({ color: "#b45309" });
    expect(screen.getByText("↑ up 50% since you started")).toBeInTheDocument();
    const summary = screen.getByRole("table", { name: "Topic summary" });
    expect(summary).toHaveTextContent("Topic");
    expect(summary).toHaveTextContent("Best");
    expect(summary).toHaveTextContent("Latest");
    expect(summary).toHaveTextContent("Attempts");
    expect(summary).toHaveTextContent("Badge");
    expect(summary).toHaveTextContent("Status");
    expect(summary).toHaveTextContent("Mensuration");
    expect(summary).toHaveTextContent("💡 Breakthrough");
    expect(summary).toHaveTextContent("Achieved ✓");
    await user.click(within(mensuration).getByRole("button", { name: "Show details ▼" }));
    expect(mensuration).toHaveTextContent("Area of a polygon 25%");
    expect(mensuration).toHaveTextContent("Cuboid 75%");
    expect(mensuration).toHaveTextContent("Up 20 points since the previous attempt");
    expect(screen.getByRole("button", { name: "View report card" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confident" }));
    expect(
      await screen.findByText("Confident and scoring high — you really do know this. Nice."),
    ).toBeInTheDocument();
    expect(recordSelfEvaluation).toHaveBeenCalledWith("math-1", "confident");
  });
});
