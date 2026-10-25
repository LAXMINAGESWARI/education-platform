import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FeedbackSubjectDetail, SubjectNode, TopicNode } from "../api/types";
import { SchoolMaterialPanel } from "./SchoolMaterialPanel";
import { SubjectDetail, SubjectRouteStatus } from "./SubjectDetail";

vi.mock("../api/feedback", () => ({
  getFeedbackHighlights: vi.fn(),
  getFeedbackSubject: vi.fn(),
}));

import { getFeedbackHighlights, getFeedbackSubject } from "../api/feedback";

function topic(overrides: Partial<TopicNode> = {}): TopicNode {
  return {
    id: "topic-1",
    title: "Fractions",
    slug: "fractions",
    sequence: 1,
    progress_percent: 40,
    complete: false,
    objectives: [],
    has_topic_lesson: true,
    topic_lesson_completed: false,
    overall_quiz: {
      id: "quiz-1",
      title: "Quiz",
      scope: "topic_mastery",
      available: true,
      unlocked: true,
      locked_reason: null,
      pass_threshold_percent: 70,
      attempt_count: 1,
      best_score_percent: 80,
      passed: true,
      in_progress_attempt_id: null,
      recent_attempts: [],
    },
    subtopics: [],
    ...overrides,
  };
}

function subject(overrides: Partial<SubjectNode> = {}): SubjectNode {
  return {
    id: "math-1",
    code: "MATH",
    name: "Mathematics",
    grade_name: "Grade 6",
    academic_period_name: "Term 1",
    progress_percent: 40,
    done: 1,
    total: 2,
    topics: [
      topic(),
      topic({
        id: "topic-2",
        title: "Decimals",
        progress_percent: 0,
        complete: false,
        overall_quiz: {
          id: "quiz-2",
          title: "Quiz",
          scope: "topic_mastery",
          available: true,
          unlocked: false,
          locked_reason: null,
          pass_threshold_percent: 70,
          attempt_count: 0,
          best_score_percent: null,
          passed: false,
          in_progress_attempt_id: null,
          recent_attempts: [],
        },
      }),
    ],
    ...overrides,
  };
}

function renderDetail(node: SubjectNode = subject()) {
  return render(
    <MemoryRouter>
      <SubjectDetail subject={node} />
    </MemoryRouter>,
  );
}

function feedbackDetail(names: string[]): FeedbackSubjectDetail {
  return { topics_needing_attention: names } as FeedbackSubjectDetail;
}

describe("SubjectDetail", () => {
  beforeEach(() => {
    vi.mocked(getFeedbackHighlights).mockResolvedValue({ due_reviews: [], trajectories: [] });
    vi.mocked(getFeedbackSubject).mockResolvedValue(feedbackDetail([]));
  });

  it("shows the subject heading, progress, and topic actions", () => {
    renderDetail();

    expect(screen.getByRole("heading", { level: 1, name: "Mathematics" })).toBeInTheDocument();
    expect(screen.getByText("School material")).toBeInTheDocument();
    expect(screen.getByText("Grade 6 · Term 1")).toBeInTheDocument();
    expect(screen.getByText("Number sense, algebra foundations, and geometry basics.")).toBeInTheDocument();
    const progress = screen.getByRole("progressbar", { name: /Subject completion, 1 of 2 topics, 40 percent/ });
    expect(progress).toHaveAttribute("aria-valuenow", "40");
    expect(progress).toHaveAttribute("aria-valuemin", "0");
    expect(progress).toHaveAttribute("aria-valuemax", "100");
    expect(screen.getByText("In progress")).toHaveClass("badge", "badge--warn");
    expect(screen.getByText("0 of 2 lessons finished")).toBeInTheDocument();
    expect(screen.getByText("1 of 2 quizzes passed")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Topics" })).toBeInTheDocument();
    expect(screen.getByText("Choose the lesson or the quiz for a topic.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Fractions" })).toHaveAttribute(
      "href",
      "/subjects/math-1/topics/topic-1",
    );
    expect(screen.getAllByRole("link", { name: /Lesson/ })[0]).toHaveAttribute(
      "href",
      "/subjects/math-1/topics/topic-1/lesson",
    );
    const topicProgress = screen.getByRole("list", { name: "Fractions progress" });
    expect(topicProgress.closest(".subtopic-card__head")).not.toBeNull();
    expect(within(topicProgress).getByText("80%", { selector: ".badge", exact: false })).toBeInTheDocument();
    expect(topicProgress).not.toHaveTextContent(">");
    expect(topicProgress).not.toHaveTextContent("›");
    expect(topicProgress.querySelector(".subtopic-card__chevron")).toBeNull();
    expect(topicProgress).toHaveAttribute("role", "list");
    expect(screen.getByRole("link", { name: "Feedback for Fractions" })).toHaveAttribute(
      "href",
      "/subjects/math-1/feedback/topics/topic-1",
    );
    expect(screen.getByRole("link", { name: "Feedback for Decimals" })).toHaveAttribute(
      "href",
      "/subjects/math-1/feedback/topics/topic-2",
    );
    expect(screen.queryByRole("link", { name: "Weak topics" })).not.toBeInTheDocument();
  });

  it("hides Weak topics when a retired topic quiz is low and needs-attention is empty", async () => {
    renderDetail(
      subject({
        topics: [
          topic({
            id: "retired",
            title: "Retired unit",
            overall_quiz: {
              ...topic().overall_quiz!,
              best_score_percent: 40,
              passed: false,
            },
          }),
        ],
      }),
    );
    await waitFor(() => expect(getFeedbackSubject).toHaveBeenCalledWith("math-1"));
    expect(screen.queryByRole("link", { name: "Weak topics" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Subject feedback" })).toBeInTheDocument();
  });

  it("hides Weak topics at exactly 70 and when every topic is unattempted", async () => {
    vi.mocked(getFeedbackSubject).mockResolvedValue(feedbackDetail([]));
    const { unmount } = renderDetail(
      subject({
        topics: [
          topic({
            overall_quiz: { ...topic().overall_quiz!, best_score_percent: 70, passed: true },
          }),
        ],
      }),
    );
    await waitFor(() => expect(getFeedbackSubject).toHaveBeenCalledWith("math-1"));
    await act(async () => {});
    expect(screen.queryByRole("link", { name: "Weak topics" })).not.toBeInTheDocument();
    unmount();

    vi.mocked(getFeedbackSubject).mockClear();
    vi.mocked(getFeedbackSubject).mockResolvedValue(feedbackDetail([]));
    renderDetail(
      subject({
        topics: [
          topic({ overall_quiz: null }),
          topic({ id: "topic-2", title: "Decimals", overall_quiz: null }),
        ],
      }),
    );
    await waitFor(() => expect(getFeedbackSubject).toHaveBeenCalledWith("math-1"));
    await act(async () => {});
    expect(screen.queryByRole("link", { name: "Weak topics" })).not.toBeInTheDocument();
  });

  it("shows Weak topics at 69.99 when needs-attention names the topic", async () => {
    vi.mocked(getFeedbackSubject).mockResolvedValue(feedbackDetail(["Fractions"]));
    renderDetail(
      subject({
        topics: [
          topic({
            overall_quiz: { ...topic().overall_quiz!, best_score_percent: 69.99, passed: false },
          }),
        ],
      }),
    );
    expect(await screen.findByRole("link", { name: "Weak topics" })).toHaveAttribute(
      "href",
      "/subjects/math-1/feedback#needs-attention",
    );
  });

  it("uses the pass mark when needs-attention data is missing", async () => {
    vi.mocked(getFeedbackSubject).mockRejectedValue(new Error("offline"));
    const { unmount } = renderDetail(
      subject({
        topics: [
          topic({
            overall_quiz: { ...topic().overall_quiz!, best_score_percent: 70, passed: true },
          }),
        ],
      }),
    );
    await waitFor(() => expect(getFeedbackSubject).toHaveBeenCalled());
    await act(async () => {});
    expect(screen.queryByRole("link", { name: "Weak topics" })).not.toBeInTheDocument();
    unmount();

    vi.mocked(getFeedbackSubject).mockRejectedValue(new Error("offline"));
    const { unmount: unmountLow } = renderDetail(
      subject({
        topics: [
          topic({
            overall_quiz: { ...topic().overall_quiz!, best_score_percent: 69.99, passed: false },
          }),
        ],
      }),
    );
    expect(await screen.findByRole("link", { name: "Weak topics" })).toBeInTheDocument();
    unmountLow();

    vi.mocked(getFeedbackSubject).mockRejectedValue(new Error("offline"));
    renderDetail(
      subject({
        topics: [topic({ overall_quiz: null })],
      }),
    );
    await waitFor(() => expect(getFeedbackSubject).toHaveBeenCalled());
    await act(async () => {});
    expect(screen.queryByRole("link", { name: "Weak topics" })).not.toBeInTheDocument();
  });

  it("shows Weak topics from the needs-attention list when directory scores are passing", async () => {
    vi.mocked(getFeedbackSubject).mockResolvedValue(feedbackDetail(["Fractions"]));
    renderDetail();
    expect(await screen.findByRole("link", { name: "Weak topics" })).toHaveAttribute(
      "href",
      "/subjects/math-1/feedback#needs-attention",
    );
  });

  it("renders an empty subject without topic cards", () => {
    renderDetail(
      subject({
        topics: [],
        done: 0,
        total: 0,
        progress_percent: 0,
      }),
    );

    expect(screen.getByRole("heading", { name: "Mathematics" })).toBeInTheDocument();
    expect(screen.getByText("No school material published yet.")).toBeInTheDocument();
    expect(screen.getByText("Coming soon")).toBeInTheDocument();
    expect(screen.getByText("Locked")).toHaveClass("badge", "badge--locked");
    expect(screen.queryByRole("heading", { name: "Topics" })).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Lesson/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Resume where you stopped/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Subject feedback" })).not.toBeInTheDocument();
  });

  it("links resume, feedback, and a due pill from saved progress", async () => {
    vi.mocked(getFeedbackSubject).mockResolvedValue(feedbackDetail(["Fractions"]));
    vi.mocked(getFeedbackHighlights).mockResolvedValue({
      due_reviews: [
        {
          subtopic_id: "st-due",
          subtopic_name: "Practice",
          topic_name: "Fractions",
          days_since_last_attempt: 12,
          due_interval_days: 7,
          remediation: null,
        },
      ],
      trajectories: [],
    });

    renderDetail(
      subject({
        topics: [
          topic({
            overall_quiz: {
              ...topic().overall_quiz!,
              best_score_percent: 55,
              passed: false,
            },
            topic_lesson_progress: {
              status: "opened",
              opened_at: "2026-08-09T21:00:00Z",
              last_opened_at: "2026-08-09T21:00:00Z",
              completed_at: null,
              last_unit_ordinal: 3,
              source_material_version_id: "ver-1",
            },
            subtopics: [
              {
                id: "st-due",
                title: "Practice",
                slug: "practice",
                sequence: 1,
                has_lesson: false,
                lesson_completed: false,
                progress_percent: 0,
                quiz: null,
              },
            ],
          }),
        ],
      }),
    );

    expect(await screen.findByRole("link", { name: /Resume where you stopped/ })).toHaveAttribute(
      "href",
      "/subjects/math-1/topics/topic-1/lesson?slide=3",
    );
    expect(screen.getByRole("link", { name: /Resume where you stopped/ })).toHaveClass("pill-btn");
    expect(screen.getByRole("link", { name: "Subject feedback" })).toHaveAttribute(
      "href",
      "/subjects/math-1/feedback",
    );
    expect(screen.getByRole("link", { name: "Due reviews" })).toHaveAttribute(
      "href",
      "/feedback?subject=math-1&section=due#feedback-due",
    );
    expect(await screen.findByRole("link", { name: "Weak topics" })).toHaveAttribute(
      "href",
      "/subjects/math-1/feedback#needs-attention",
    );
    expect(screen.getByRole("link", { name: /Lesson/ })).toHaveClass("pill-btn");
    expect(screen.getByRole("link", { name: /Lesson/ })).toHaveAttribute(
      "href",
      "/subjects/math-1/topics/topic-1/lesson?slide=3",
    );
    expect(await screen.findByText("Due")).toHaveClass("badge", "badge--warn");
    expect(screen.getByText("Slide 3")).toBeInTheDocument();
  });

  it("removes the Due pill when a later highlight list is empty", async () => {
    const dueSubject = subject({
      topics: [
        topic({
          subtopics: [
            {
              id: "st-due",
              title: "Practice",
              slug: "practice",
              sequence: 1,
              has_lesson: false,
              lesson_completed: false,
              progress_percent: 0,
              quiz: null,
            },
          ],
        }),
      ],
    });
    vi.mocked(getFeedbackHighlights).mockResolvedValue({
      due_reviews: [
        {
          subtopic_id: "st-due",
          subtopic_name: "Practice",
          topic_name: "Fractions",
          days_since_last_attempt: 12,
          due_interval_days: 7,
          remediation: null,
        },
      ],
      trajectories: [],
    });
    const { rerender } = renderDetail(dueSubject);
    expect(await screen.findByText("Due")).toBeInTheDocument();

    vi.mocked(getFeedbackHighlights).mockResolvedValue({ due_reviews: [], trajectories: [] });
    rerender(
      <MemoryRouter>
        <SubjectDetail subject={{ ...dueSubject }} />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.queryByText("Due")).not.toBeInTheDocument();
    });
  });
});

describe("SubjectRouteStatus", () => {
  it("renders loading, error, and missing states", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    const { rerender } = render(
      <MemoryRouter>
        <SubjectRouteStatus kind="loading" />
      </MemoryRouter>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Loading subject…");

    rerender(
      <MemoryRouter>
        <SubjectRouteStatus kind="error" message="Could not load subjects." onRetry={onRetry} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load subjects.");
    expect(screen.getByRole("link", { name: "Back to subjects" })).toHaveAttribute("href", "/");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledOnce();

    rerender(
      <MemoryRouter>
        <SubjectRouteStatus kind="missing" />
      </MemoryRouter>,
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "This subject is not on your learning directory.",
    );
    expect(screen.getByRole("link", { name: "Back to subjects" })).toHaveAttribute("href", "/");
  });
});

describe("SchoolMaterialPanel", () => {
  it("renders an empty topic list", () => {
    render(
      <MemoryRouter>
        <SchoolMaterialPanel subjectId="math-1" topics={[]} />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "Topics" })).toBeInTheDocument();
    expect(screen.getByText("No topics published for this subject yet.")).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });
});
