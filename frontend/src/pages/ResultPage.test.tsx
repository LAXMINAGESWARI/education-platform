import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AttemptResult } from "../api/types";
import { ResultPage } from "./ResultPage";

const getAttempt = vi.fn();
const fetchLearningDirectory = vi.fn();

vi.mock("../api/attempts", () => ({
  getAttempt: (...args: unknown[]) => getAttempt(...args),
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

const result: AttemptResult = {
  id: "att-1",
  quiz_id: "quiz-1",
  target_id: "st-1",
  scope: "subtopic_mastery",
  attempt_number: 1,
  status: "scored",
  started_at: null,
  submitted_at: null,
  scored_at: null,
  score_raw: "2/2",
  score_percent: 100,
  pass_threshold_percent: 70,
  passed: true,
  review_available: true,
  answers: [
    {
      question_number: 1,
      selected_option_label: "A",
      is_correct: true,
      marks_awarded: 1,
    },
  ],
};

describe("ResultPage", () => {
  beforeEach(() => {
    getAttempt.mockReset();
    fetchLearningDirectory.mockReset();
    getAttempt.mockResolvedValue(result);
    fetchLearningDirectory.mockResolvedValue({
      subjects: [
        {
          id: "subj-1",
          code: "MATH",
          name: "Mathematics",
          grade_name: "Grade 8",
          academic_period_name: "2026-27",
          progress_percent: 100,
          topics: [
            {
              id: "topic-1",
              title: "Approved Materials",
              slug: "approved_materials",
              sequence: 1,
              progress_percent: 100,
              complete: false,
              objectives: [],
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
              subtopics: [
                {
                  id: "st-1",
                  title: "Properties of Rectangles and Squares",
                  slug: "rectangles_squares_properties",
                  sequence: 1,
                  has_lesson: true,
                  lesson_completed: true,
                  progress_percent: 100,
                  quiz: {
                    id: "quiz-1",
                    title: "Quiz",
                    scope: "subtopic_mastery",
                    available: true,
                    unlocked: true,
                    locked_reason: null,
                    pass_threshold_percent: 70,
                    attempt_count: 1,
                    best_score_percent: 100,
                    passed: true,
                    in_progress_attempt_id: null,
                    recent_attempts: [],
                  },
                },
              ],
            },
          ],
        },
      ],
    });
  });

  it("shows topic actions and overall unlock dialog", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter
        initialEntries={["/attempts/att-1"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/attempts/:attemptId" element={<ResultPage />} />
          <Route path="/subjects/:subjectId" element={<div>Subject hub</div>} />
          <Route
            path="/subjects/:subjectId/subtopics/:subtopicId/lesson"
            element={<div>Unit quiz tab</div>}
          />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Your score")).toBeInTheDocument();
    expect(
      screen.getByText(/All subtopic quizzes passed. The overall topic quiz is now unlocked./),
    ).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Overall quiz unlocked" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review lesson" })).toHaveAttribute(
      "href",
      "/subjects/subj-1/subtopics/st-1/lesson/slides?from=start",
    );
    expect(screen.getByRole("link", { name: "Properties of Rectangles and Squares" })).toHaveAttribute(
      "href",
      "/subjects/subj-1/subtopics/st-1/lesson",
    );
    expect(screen.queryByRole("link", { name: /Back to quiz/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Stay here" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does not claim the overall quiz just unlocked after a subtopic pass when a topic lesson exists", async () => {
    fetchLearningDirectory.mockResolvedValue({
      subjects: [
        {
          id: "subj-1",
          code: "MATH",
          name: "Mathematics",
          grade_name: "Grade 8",
          academic_period_name: "2026-27",
          progress_percent: 100,
          topics: [
            {
              id: "topic-1",
              title: "Approved Materials",
              slug: "approved_materials",
              sequence: 1,
              progress_percent: 100,
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
              subtopics: [
                {
                  id: "st-1",
                  title: "Properties of Rectangles and Squares",
                  slug: "rectangles_squares_properties",
                  sequence: 1,
                  has_lesson: true,
                  lesson_completed: true,
                  progress_percent: 100,
                  quiz: {
                    id: "quiz-1",
                    title: "Quiz",
                    scope: "subtopic_mastery",
                    available: true,
                    unlocked: true,
                    locked_reason: null,
                    pass_threshold_percent: 70,
                    attempt_count: 1,
                    best_score_percent: 100,
                    passed: true,
                    in_progress_attempt_id: null,
                    recent_attempts: [],
                  },
                },
              ],
            },
          ],
        },
      ],
    });

    render(
      <MemoryRouter
        initialEntries={["/attempts/att-1"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/attempts/:attemptId" element={<ResultPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Your score")).toBeInTheDocument();
    expect(
      screen.queryByText(/All subtopic quizzes passed. The overall topic quiz is now unlocked./),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Overall quiz unlocked" })).not.toBeInTheDocument();
    expect(screen.queryByText(/correct_rationale/i)).not.toBeInTheDocument();
  });

  it("reviews a topic mastery attempt back to the published topic lesson", async () => {
    getAttempt.mockResolvedValue({
      ...result,
      id: "att-topic",
      quiz_id: "overall-1",
      target_id: "topic-1",
      scope: "topic_mastery",
    });
    fetchLearningDirectory.mockResolvedValue({
      subjects: [
        {
          id: "subj-1",
          code: "MATH",
          name: "Mathematics",
          grade_name: "Grade 8",
          academic_period_name: "2026-27",
          progress_percent: 100,
          topics: [
            {
              id: "topic-1",
              title: "Approved Materials",
              slug: "approved_materials",
              sequence: 1,
              progress_percent: 100,
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
                attempt_count: 1,
                best_score_percent: 100,
                passed: true,
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
        initialEntries={["/attempts/att-topic"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/attempts/:attemptId" element={<ResultPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Your score")).toBeInTheDocument();
    expect(screen.getByText(/overall quiz/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review lesson" })).toHaveAttribute(
      "href",
      "/subjects/subj-1/topics/topic-1/lesson",
    );
    expect(screen.getByRole("link", { name: "Approved Materials" })).toHaveAttribute(
      "href",
      "/subjects/subj-1/topics/topic-1",
    );
    expect(screen.getByText(/Correct answer keys stay hidden/)).toBeInTheDocument();
    expect(screen.getByText(/1 question in this attempt/)).toBeInTheDocument();
  });
});
