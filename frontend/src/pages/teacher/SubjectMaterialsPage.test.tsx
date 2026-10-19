import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SubjectMaterialsPage } from "./SubjectMaterialsPage";
import type { StudentInsightPage } from "../../api/insights";
import type { LearningDirectory, LessonMaterial, QuizMaterial, QuizSummary } from "../../api/types";

const fetchStudentInsights = vi.fn();
const fetchLearningDirectory = vi.fn();
const getSubtopicMaterial = vi.fn();
const getTopicMaterial = vi.fn();
const getSubtopicQuiz = vi.fn();
const startAttempt = vi.fn();

vi.mock("../../api/insights", () => ({
  fetchStudentInsights: (...args: unknown[]) => fetchStudentInsights(...args),
}));

vi.mock("../../api/materials", () => ({
  fetchLearningDirectory: () => fetchLearningDirectory(),
  getSubtopicMaterial: (...args: unknown[]) => getSubtopicMaterial(...args),
  getTopicMaterial: (...args: unknown[]) => getTopicMaterial(...args),
  getSubtopicQuiz: (...args: unknown[]) => getSubtopicQuiz(...args),
}));

vi.mock("../../api/attempts", () => ({
  startAttempt: (...args: unknown[]) => startAttempt(...args),
}));

function insightPage(): StudentInsightPage {
  return {
    scope_description: "1 class",
    rows_returned: 1,
    items: [
      {
        student_id: "a",
        full_name: "Aisha Rahman",
        student_identifier: "S-0001",
        grade: "Grade 8",
        section: "8A",
        subject: "Mathematics",
        academic_period: "Term 1 2026",
        quizzes_taken: 1,
        quizzes_passed: 0,
        mastery_percent: 40,
        lessons_completed: 1,
        attendance_percent: 80,
      },
    ],
  };
}

function quizSummary(over: Partial<QuizSummary> = {}): QuizSummary {
  return {
    id: "quiz-1",
    title: "Fractions check",
    scope: "subtopic_mastery",
    available: true,
    unlocked: true,
    locked_reason: null,
    pass_threshold_percent: 70,
    attempt_count: 0,
    best_score_percent: null,
    passed: false,
    in_progress_attempt_id: null,
    recent_attempts: [],
    ...over,
  };
}

function directory(): LearningDirectory {
  return {
    subjects: [
      {
        id: "subj-math",
        code: "MATH",
        name: "Mathematics",
        grade_name: "Grade 8",
        academic_period_name: "Term 1 2026",
        progress_percent: 40,
        topics: [
          {
            id: "topic-1",
            title: "Number",
            slug: "number",
            sequence: 1,
            progress_percent: 40,
            complete: false,
            objectives: [],
            subtopics: [
              {
                id: "st-1",
                title: "Fractions",
                slug: "fractions",
                sequence: 1,
                has_lesson: true,
                lesson_completed: false,
                progress_percent: 0,
                quiz: quizSummary(),
              },
            ],
            overall_quiz: quizSummary({
              id: "quiz-topic",
              title: "Number mastery",
              scope: "topic_mastery",
            }),
          },
          {
            id: "topic-frac",
            title: "Fractions",
            slug: "fractions",
            sequence: 2,
            progress_percent: 0,
            complete: false,
            objectives: [],
            has_topic_lesson: true,
            subtopics: [
              {
                id: "st-eq",
                title: "Equivalent fractions",
                slug: "equivalent-fractions",
                sequence: 1,
                has_lesson: false,
                lesson_completed: false,
                progress_percent: 0,
                quiz: null,
              },
            ],
            overall_quiz: quizSummary({
              id: "quiz-frac",
              title: "Fractions topic mastery",
              scope: "topic_mastery",
            }),
          },
        ],
      },
    ],
  };
}

function lesson(): LessonMaterial {
  return {
    id: "st-1",
    title: "Fractions lesson",
    markdown: "## Slide 1",
    slides: [{ number: 1, title: "What is a fraction?", content: "A part of a whole." }],
    source_material_version_id: "v1",
    progress: null,
    quiz_unlocked: false,
    quiz_id: "quiz-1",
  };
}

function quizMaterial(): QuizMaterial {
  return {
    id: "quiz-1",
    title: "Fractions check",
    questions: [
      {
        number: 1,
        difficulty: "Easy",
        prompt: "What is 1/2 + 1/2?",
        options: [
          { label: "A", text: "1" },
          { label: "B", text: "1/4" },
          { label: "C", text: "2" },
          { label: "D", text: "0" },
        ],
      },
    ],
    pass_threshold_percent: 70,
    duration_seconds: null,
    max_attempts: null,
    result_release_mode: "immediate",
  };
}

function renderPage() {
  return render(
    <MemoryRouter
      initialEntries={["/teacher/classes/grade-8-8a/subjects/subj-math"]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route
          path="/teacher/classes/:sectionId/subjects/:subjectId"
          element={<SubjectMaterialsPage />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("SubjectMaterialsPage", () => {
  beforeEach(() => {
    fetchStudentInsights.mockReset();
    fetchLearningDirectory.mockReset();
    getSubtopicMaterial.mockReset();
    getTopicMaterial.mockReset();
    getSubtopicQuiz.mockReset();
    startAttempt.mockReset();
    fetchStudentInsights.mockResolvedValue(insightPage());
    fetchLearningDirectory.mockResolvedValue(directory());
    getSubtopicMaterial.mockResolvedValue(lesson());
    getTopicMaterial.mockResolvedValue(lesson());
    getSubtopicQuiz.mockResolvedValue(quizMaterial());
  });

  it("loads live directory units instead of fixtures", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { level: 1, name: "Mathematics" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Topics" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Units" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Number/ })).toHaveTextContent("1 lesson · 2 quizzes");
    expect(screen.getByRole("button", { name: /1 lesson · 1 quiz/ })).toBeInTheDocument();
    expect(screen.getByText(/Read-only — no attempts/)).toBeInTheDocument();
  });

  it("links the active unit to the teacher generation review route", async () => {
    renderPage();
    const link = await screen.findByRole("link", { name: "Review generation" });
    expect(link).toHaveAttribute("href", "/teacher/topics/topic-1/generation");
  });

  it("opens a published lesson from the live material GET", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: /Fractions Published/ }));

    expect(getSubtopicMaterial).toHaveBeenCalledWith("st-1");
    expect(await screen.findByRole("article", { name: "Lesson preview" })).toBeInTheDocument();
    expect(screen.getByText("A part of a whole.")).toBeInTheDocument();
    expect(startAttempt).not.toHaveBeenCalled();
  });

  it("shows released quiz questions read-only and does not start an attempt", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("heading", { level: 1, name: "Mathematics" });

    await user.click(screen.getByRole("tab", { name: "Quizzes" }));
    await user.click(screen.getByRole("button", { name: /Fractions check/ }));

    expect(getSubtopicQuiz).toHaveBeenCalledWith("st-1");
    expect(await screen.findByText("What is 1/2 + 1/2?")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /submit/i })).not.toBeInTheDocument();
    expect(startAttempt).not.toHaveBeenCalled();
  });

  it("opens the topic lesson from the topic row", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: /1 lesson · 1 quiz/ }));
    await user.click(screen.getByRole("button", { name: /Fractions lesson/ }));

    expect(getTopicMaterial).toHaveBeenCalledWith("topic-frac");
    expect(await screen.findByRole("article", { name: "Lesson preview" })).toBeInTheDocument();
    expect(startAttempt).not.toHaveBeenCalled();
  });
});
