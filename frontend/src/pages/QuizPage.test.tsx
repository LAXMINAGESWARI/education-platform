import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { StartAttemptResponse } from "../api/types";
import { QuizPage } from "./QuizPage";

const startAttempt = vi.fn();
const submitAttempt = vi.fn();
const fetchLearningDirectory = vi.fn();

vi.mock("../api/attempts", () => ({
  startAttempt: (...args: unknown[]) => startAttempt(...args),
  submitAttempt: (...args: unknown[]) => submitAttempt(...args),
  buildSubmitPayload: (answers: Record<number, string>) => ({
    answers: Object.entries(answers).map(([question_number, selected_option_label]) => ({
      question_number: Number(question_number),
      selected_option_label,
    })),
  }),
}));

vi.mock("../api/materials", () => ({
  fetchLearningDirectory: (...args: unknown[]) => fetchLearningDirectory(...args),
}));

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    user: { full_name: "Asha Student" },
    enrollments: {
      grade_enrollments: [{ status: "active", grade_name: "Grade 8" }],
      subject_enrollments: [{ status: "active", subject_name: "Mathematics" }],
    },
    enrolled: true,
    signOut: vi.fn(),
  }),
}));

const attempt: StartAttemptResponse = {
  id: "att-1",
  quiz_id: "quiz-1",
  quiz_version_id: "qv-1",
  attempt_number: 1,
  status: "in_progress",
  started_at: null,
  deadline_at: null,
  pass_threshold_percent: 70,
  result_release_mode: "immediate",
  title: "Properties of Rectangles and Squares Quiz",
  scope: "subtopic_mastery",
  target_id: "st-1",
  questions: [
    {
      number: 1,
      difficulty: null,
      prompt: "What is a square?",
      options: [
        { label: "A", text: "A rectangle with equal sides" },
        { label: "B", text: "A circle" },
      ],
    },
  ],
};

describe("QuizPage", () => {
  beforeEach(() => {
    startAttempt.mockReset();
    submitAttempt.mockReset();
    fetchLearningDirectory.mockReset();
    startAttempt.mockResolvedValue(attempt);
    submitAttempt.mockReturnValue(new Promise(() => undefined));
    fetchLearningDirectory.mockResolvedValue({
      subjects: [
        {
          id: "subj-1",
          code: "MATH",
          name: "Mathematics",
          grade_name: "Grade 8",
          academic_period_name: "2026-27",
          progress_percent: 0,
          topics: [
            {
              id: "topic-1",
              title: "Approved Materials",
              slug: "approved_materials",
              sequence: 1,
              progress_percent: 0,
              complete: false,
              objectives: [],
              overall_quiz: null,
              subtopics: [
                {
                  id: "st-1",
                  title: "Properties of Rectangles and Squares",
                  slug: "rectangles_squares_properties",
                  sequence: 1,
                  has_lesson: true,
                  lesson_completed: true,
                  progress_percent: 50,
                  quiz: null,
                },
              ],
            },
          ],
        },
      ],
    });
  });

  it("numbers a ten-question draw and shows that served count", async () => {
    startAttempt.mockResolvedValue({
      ...attempt,
      scope: "topic_mastery",
      questions: Array.from({ length: 10 }, (_, index) => ({
        number: index + 1,
        difficulty: index < 4 ? "Easy" : index < 7 ? "Medium" : "Hard",
        prompt: `Prompt ${index + 1}`,
        options: [
          { label: "A", text: "First" },
          { label: "B", text: "Second" },
        ],
      })),
    });

    render(
      <MemoryRouter
        initialEntries={["/quizzes/quiz-1"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/quizzes/:quizId" element={<QuizPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Prompt 1")).toBeInTheDocument();
    expect(screen.getByText(/Question 1 of 10/)).toBeInTheDocument();
    expect(screen.getByText("Easy")).toBeInTheDocument();
  });

  it("auto-starts the attempt and shows question options", async () => {
    render(
      <MemoryRouter
        initialEntries={["/quizzes/quiz-1"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/quizzes/:quizId" element={<QuizPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("What is a square?")).toBeInTheDocument();
    expect(startAttempt).toHaveBeenCalledWith("quiz-1");
    expect(screen.queryByText("Ready to begin?")).not.toBeInTheDocument();
    expect(screen.getByText("A rectangle with equal sides")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Properties of Rectangles and Squares" }),
    ).toHaveAttribute("href", "/subjects/subj-1/subtopics/st-1/lesson");
    expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toHaveTextContent(
      /Mathematics\s*\/\s*Properties of Rectangles and Squares\s*\/\s*Quiz$/,
    );
    expect(screen.queryByRole("link", { name: /Back to quiz/i })).not.toBeInTheDocument();
  });

  it("blocks copy, cut, and paste on the question and options", async () => {
    render(
      <MemoryRouter
        initialEntries={["/quizzes/quiz-1"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/quizzes/:quizId" element={<QuizPage />} />
        </Routes>
      </MemoryRouter>,
    );

    const prompt = await screen.findByText("What is a square?");
    expect(prompt.closest(".quiz-lock")).not.toBeNull();
    expect(fireEvent.copy(prompt)).toBe(false);
    expect(fireEvent.paste(prompt)).toBe(false);
    expect(fireEvent.cut(screen.getByRole("radio", { name: /A rectangle with equal sides/i }))).toBe(
      false,
    );
    expect(
      fireEvent.copy(screen.getByRole("heading", { name: "Properties of Rectangles and Squares Quiz" })),
    ).toBe(true);
    expect(screen.getByText("Copy and paste are disabled during quizzes")).toBeInTheDocument();
  });

  it("does not submit the same attempt twice on a double click", async () => {
    render(
      <MemoryRouter
        initialEntries={["/quizzes/quiz-1"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/quizzes/:quizId" element={<QuizPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("What is a square?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: /A rectangle with equal sides/i }));
    const submit = screen.getByRole("button", { name: "Submit quiz" });
    fireEvent.click(submit);
    fireEvent.click(submit);
    expect(submitAttempt).toHaveBeenCalledTimes(1);
  });

  it("links a topic mastery quiz back to the published topic lesson", async () => {
    startAttempt.mockResolvedValue({
      ...attempt,
      quiz_id: "overall-1",
      title: "Overall topic quiz",
      scope: "topic_mastery",
      target_id: "topic-1",
    });
    fetchLearningDirectory.mockResolvedValue({
      subjects: [
        {
          id: "subj-1",
          code: "MATH",
          name: "Mathematics",
          grade_name: "Grade 8",
          academic_period_name: "2026-27",
          progress_percent: 0,
          topics: [
            {
              id: "topic-1",
              title: "Approved Materials",
              slug: "approved_materials",
              sequence: 1,
              progress_percent: 0,
              complete: false,
              objectives: [],
              has_topic_lesson: true,
              topic_source_material_version_id: "ver-topic-1",
              overall_quiz: {
                id: "overall-1",
                title: "Overall",
                scope: "topic_mastery",
                available: true,
                unlocked: true,
                locked_reason: null,
                pass_threshold_percent: 70,
                attempt_count: 0,
                best_score_percent: null,
                passed: false,
                in_progress_attempt_id: null,
                recent_attempts: [],
              },
              subtopics: [],
            },
          ],
        },
      ],
    });

    render(
      <MemoryRouter
        initialEntries={["/quizzes/overall-1"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/quizzes/:quizId" element={<QuizPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("What is a square?")).toBeInTheDocument();
    expect(screen.getByText("Quiz · Attempt 1 · overall")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Approved Materials" })).toHaveAttribute(
      "href",
      "/subjects/subj-1/topics/topic-1/lesson",
    );
    expect(screen.queryByText(/correct_rationale/i)).not.toBeInTheDocument();
  });
});
