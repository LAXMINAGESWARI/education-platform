import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { HomePage, clearLearningDirectoryCache } from "./HomePage";

vi.mock("../components/AppShell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock("../api/materials", () => ({
  fetchLearningDirectory: vi.fn(),
}));

vi.mock("../api/feedback", () => ({
  getFeedbackHighlights: vi.fn().mockResolvedValue({ due_reviews: [], trajectories: [] }),
}));

import { fetchLearningDirectory } from "../api/materials";

const mockDirectory = {
  subjects: [
    {
      id: "math-1",
      code: "MATH",
      name: "Mathematics",
      grade_name: "Grade 8",
      academic_period_name: "2026-27",
      progress_percent: 25,
      topics: [
        {
          id: "topic-1",
          title: "Approved Materials",
          slug: "approved_materials",
          sequence: 1,
          progress_percent: 25,
          complete: false,
          objectives: ["Define rectangles and squares."],
          overall_quiz: {
            id: "overall-1",
            title: "Overall",
            scope: "topic_mastery",
            available: true,
            unlocked: false,
            locked_reason: "Pass all subtopic quizzes first",
            pass_threshold_percent: 70,
            attempt_count: 0,
            best_score_percent: null,
            passed: false,
            in_progress_attempt_id: null,
            recent_attempts: [],
          },
          subtopics: [
            {
              id: "st-1",
              title: "Properties of Rectangles and Squares",
              slug: "rectangles_squares_properties",
              sequence: 1,
              has_lesson: true,
              lesson_completed: true,
              progress_percent: 50,
              progress: {
                status: "opened",
                opened_at: "2026-08-09T21:00:00Z",
                last_opened_at: "2026-08-09T21:00:00Z",
                completed_at: null,
                last_unit_ordinal: 4,
                source_material_version_id: "ver-1",
              },
              quiz: {
                id: "quiz-1",
                title: "Quiz",
                scope: "subtopic_mastery",
                available: true,
                unlocked: true,
                locked_reason: null,
                pass_threshold_percent: 70,
                attempt_count: 1,
                best_score_percent: 50,
                passed: false,
                in_progress_attempt_id: null,
                recent_attempts: [
                  {
                    id: "att-1",
                    attempt_number: 1,
                    status: "submitted",
                    score_percent: 50,
                    passed: false,
                    started_at: "2026-08-09T21:00:00Z",
                    submitted_at: "2026-08-09T21:30:00Z",
                  },
                ],
              },
            },
          ],
        },
      ],
    },
  ],
};

describe("HomePage", () => {
  beforeEach(() => {
    clearLearningDirectoryCache();
    vi.mocked(fetchLearningDirectory).mockResolvedValue(mockDirectory as never);
  });

  it("renders mock subjects dashboard without stage workflow chrome", async () => {
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "Your subjects" })).toBeInTheDocument();
    expect(screen.queryByText("Student dashboard · demo data")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Mathematics" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Mathematics/ })).toHaveAttribute("href", "/subjects/math-1");
    expect(screen.getByText("Number sense, algebra foundations, and geometry basics.")).toBeInTheDocument();
    expect(screen.getByText("0/1 topics complete · 0%")).toBeInTheDocument();
    expect(screen.getByText("School material")).toHaveClass("badge", "badge--info");
    expect(screen.getByRole("heading", { name: "Science" })).toBeInTheDocument();
    expect(screen.getAllByText("Coming soon").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Locked").length).toBeGreaterThan(0);
    expect(screen.queryByRole("table", { name: "Subject progress" })).not.toBeInTheDocument();
    expect(screen.queryByText(/1\.0 Enroll/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Topics$/)).not.toBeInTheDocument();
  });

  it("shows school material on the subject view", async () => {
    render(
      <MemoryRouter
        initialEntries={["/subjects/math-1"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/subjects/:subjectId" element={<HomePage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { level: 1, name: "Mathematics" })).toBeInTheDocument();
    expect(screen.getByText("School material")).toBeInTheDocument();
    expect(screen.getByText("Grade 8 · 2026-27")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: /Subject completion/ })).toHaveAttribute(
      "aria-valuenow",
      "0",
    );
    expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toHaveTextContent(
      /Subjects\s*\/\s*Mathematics/,
    );
    expect(screen.queryByRole("link", { name: /Back to subjects/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Personal material" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Topics" })).toBeInTheDocument();
    expect(screen.getByText("Choose the lesson or the quiz for a topic.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Units" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Subtopics" })).not.toBeInTheDocument();
    expect(screen.queryByText("Properties of Rectangles and Squares")).not.toBeInTheDocument();
    expect(screen.getByText("Approved Materials")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Mathematics quiz" })).not.toBeInTheDocument();
    expect(screen.getByText("0/1 topics complete · 0%")).toBeInTheDocument();

    expect(screen.getByRole("link", { name: /Approved Materials/ })).toHaveAttribute(
      "href",
      "/subjects/math-1/topics/topic-1",
    );
  });

  it("surfaces a published topic lesson and unlocks the overall quiz from that lesson", async () => {
    vi.mocked(fetchLearningDirectory).mockResolvedValue({
      ...mockDirectory,
      subjects: [
        {
          ...mockDirectory.subjects[0],
          topics: [
            {
              ...mockDirectory.subjects[0].topics[0],
              has_topic_lesson: true,
              topic_lesson_completed: false,
              topic_source_material_version_id: "ver-topic-1",
              overall_quiz: {
                ...mockDirectory.subjects[0].topics[0].overall_quiz!,
                unlocked: true,
                locked_reason: null,
              },
            },
          ],
        },
      ],
    } as never);

    render(
      <MemoryRouter
        initialEntries={["/subjects/math-1"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/subjects/:subjectId" element={<HomePage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "Topics" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Approved Materials" })).toHaveAttribute(
      "href",
      "/subjects/math-1/topics/topic-1",
    );
    expect(screen.getByRole("link", { name: /Lesson/ })).toHaveAttribute(
      "href",
      "/subjects/math-1/topics/topic-1/lesson",
    );
    expect(screen.getByText("Start")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Quiz/ })).toHaveAttribute(
      "href",
      "/quizzes/overall-1",
    );
    expect(screen.queryByText("Properties of Rectangles and Squares")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Units" })).not.toBeInTheDocument();
    expect(screen.queryByText(/correct_rationale/i)).not.toBeInTheDocument();
  });

  it("shows Continue and Retake on a topic the student has already opened", async () => {
    vi.mocked(fetchLearningDirectory).mockResolvedValue({
      ...mockDirectory,
      subjects: [
        {
          ...mockDirectory.subjects[0],
          topics: [
            {
              ...mockDirectory.subjects[0].topics[0],
              title: "Mensuration",
              has_topic_lesson: true,
              topic_lesson_completed: false,
              topic_lesson_progress: {
                status: "opened",
                opened_at: "2026-08-09T21:00:00Z",
                last_opened_at: "2026-08-09T21:00:00Z",
                completed_at: null,
                last_unit_ordinal: 3,
                source_material_version_id: "ver-topic-1",
              },
              overall_quiz: {
                ...mockDirectory.subjects[0].topics[0].overall_quiz!,
                unlocked: true,
                locked_reason: null,
                attempt_count: 1,
                best_score_percent: 64,
                recent_attempts: [
                  {
                    id: "att-1",
                    attempt_number: 1,
                    status: "submitted",
                    score_percent: 64,
                    passed: false,
                    started_at: "2026-08-09T22:00:00Z",
                    submitted_at: "2026-08-09T22:20:00Z",
                  },
                ],
              },
            },
          ],
        },
      ],
    } as never);

    render(
      <MemoryRouter
        initialEntries={["/subjects/math-1"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/subjects/:subjectId" element={<HomePage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Continue")).toBeInTheDocument();
    expect(screen.getByText("In progress · slide 3")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Lesson/ })).toHaveAttribute(
      "href",
      "/subjects/math-1/topics/topic-1/lesson?slide=3",
    );
    expect(screen.getByRole("link", { name: /Lesson/ })).toHaveClass("pill-btn");
    expect(screen.getByRole("link", { name: /Quiz/ })).toHaveAttribute(
      "href",
      "/quizzes/overall-1",
    );
    expect(screen.getByText("Retake")).toBeInTheDocument();
    expect(screen.getByText("Last score 64%")).toBeInTheDocument();
  });

  it("locks the topic quiz with complete-the-topic-lesson copy", async () => {
    vi.mocked(fetchLearningDirectory).mockResolvedValue({
      ...mockDirectory,
      subjects: [
        {
          ...mockDirectory.subjects[0],
          topics: [
            {
              ...mockDirectory.subjects[0].topics[0],
              title: "Mensuration",
              has_topic_lesson: true,
              topic_lesson_completed: false,
              topic_source_material_version_id: "ver-topic-1",
              overall_quiz: {
                ...mockDirectory.subjects[0].topics[0].overall_quiz!,
                unlocked: false,
                locked_reason: "unlocks when this lesson is published",
              },
            },
          ],
        },
      ],
    } as never);

    render(
      <MemoryRouter
        initialEntries={["/subjects/math-1"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/subjects/:subjectId" element={<HomePage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Complete the topic lesson first.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Lesson/ })).toHaveAttribute(
      "href",
      "/subjects/math-1/topics/topic-1/lesson",
    );
    expect(screen.queryByRole("link", { name: /Quiz/ })).not.toBeInTheDocument();
    expect(screen.getByText("Take quiz")).toBeInTheDocument();
    expect(screen.queryByText(/unlocks when/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/published/i)).not.toBeInTheDocument();
  });
});
