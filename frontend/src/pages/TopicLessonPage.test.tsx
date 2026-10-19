import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/types";
import { clearTopicLessonCache } from "../lib/useTopicLesson";
import { TopicLessonPage } from "./TopicLessonPage";
import { TopicPageRedirect } from "./TopicPageRedirect";

const getTopicMaterial = vi.fn();
const fetchLearningDirectory = vi.fn();
const updateTopicMaterialProgress = vi.fn();
const getGenerationRun = vi.fn();

vi.mock("../api/materials", () => ({
  getTopicMaterial: (...args: unknown[]) => getTopicMaterial(...args),
  fetchLearningDirectory: (...args: unknown[]) => fetchLearningDirectory(...args),
  updateTopicMaterialProgress: (...args: unknown[]) => updateTopicMaterialProgress(...args),
}));

vi.mock("../api/generation", () => ({
  getGenerationRun: (...args: unknown[]) => getGenerationRun(...args),
  listGenerationRuns: vi.fn(),
  publishGenerationRun: vi.fn(),
  rejectGenerationItems: vi.fn(),
}));

const lessonPayload = {
  id: "topic-1",
  title: "Rectangles and squares",
  markdown: "# Rectangles and squares\n\nA rectangle has four right angles.",
  slides: [],
  source_material_version_id: "ver-topic-1",
  quiz_unlocked: true,
  quiz_id: "overall-1",
  progress: null,
};

const directory = {
  subjects: [
    {
      id: "subj-1",
      code: "MATH",
      name: "Mathematics",
      grade_name: "Grade 8",
      academic_period_name: "2026-27",
      progress_percent: 40,
      topics: [
        {
          id: "topic-1",
          title: "Approved Materials",
          slug: "approved_materials",
          sequence: 1,
          progress_percent: 40,
          complete: false,
          objectives: [],
          has_topic_lesson: true,
          topic_lesson_completed: false,
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
              progress_percent: 50,
              quiz: {
                id: "quiz-1",
                title: "Quiz",
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
              },
            },
          ],
        },
      ],
    },
  ],
};

function renderTopicLesson(path = "/subjects/subj-1/topics/topic-1/lesson") {
  return render(
    <MemoryRouter
      initialEntries={[path]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route
          path="/subjects/:subjectId/topics/:topicId/lesson"
          element={<TopicLessonPage />}
        />
        <Route path="/subjects/:subjectId/topics/:topicId" element={<TopicPageRedirect />} />
        <Route path="/subjects/:subjectId" element={<div>Subject hub</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("TopicLessonPage", () => {
  beforeEach(() => {
    clearTopicLessonCache();
    getTopicMaterial.mockReset();
    fetchLearningDirectory.mockReset();
    updateTopicMaterialProgress.mockReset();
    getGenerationRun.mockReset();
    getTopicMaterial.mockResolvedValue(lessonPayload);
    updateTopicMaterialProgress.mockResolvedValue({ status: "completed" });
    fetchLearningDirectory.mockResolvedValue(directory);
  });

  it("loads the published topic lesson and unlocks the topic quiz without keys", async () => {
    renderTopicLesson();

    expect(await screen.findByRole("heading", { name: "Rectangles and squares" })).toBeInTheDocument();
    expect(screen.getByText(/A rectangle has four right angles/)).toBeInTheDocument();
    expect(getTopicMaterial).toHaveBeenCalledWith("topic-1");
    expect(getGenerationRun).not.toHaveBeenCalled();
    expect(screen.getByRole("link", { name: "Start quiz" })).toHaveAttribute(
      "href",
      "/quizzes/overall-1",
    );
    expect(screen.getByText("Unlocked")).toBeInTheDocument();
    expect(screen.queryByText(/correct_rationale/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/qa_items/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /answer key/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/Finish every slide to unlock/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Mathematics" })).toHaveAttribute(
      "href",
      "/subjects/subj-1",
    );
  });

  it("explains when the topic lesson is not published yet", async () => {
    getTopicMaterial.mockRejectedValue(new ApiError("Lesson not found", 404, null));
    renderTopicLesson();

    expect(await screen.findByRole("alert")).toHaveTextContent(/Lesson not found|not published/i);
    expect(screen.getByRole("link", { name: "Back to subject" })).toHaveAttribute(
      "href",
      "/subjects/subj-1",
    );
    expect(getGenerationRun).not.toHaveBeenCalled();
  });
});
