import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { SubjectNode, TopicNode } from "../api/types";
import { SchoolMaterialPanel } from "./SchoolMaterialPanel";
import { SubjectDetail, SubjectRouteStatus } from "./SubjectDetail";

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

describe("SubjectDetail", () => {
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
  });
});

describe("SubjectRouteStatus", () => {
  it("renders loading, error, and missing states", () => {
    const { rerender } = render(
      <MemoryRouter>
        <SubjectRouteStatus kind="loading" />
      </MemoryRouter>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Loading subject…");

    rerender(
      <MemoryRouter>
        <SubjectRouteStatus kind="error" message="Could not load subjects." />
      </MemoryRouter>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load subjects.");

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
