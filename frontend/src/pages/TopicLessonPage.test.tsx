import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/types";
import { clearTopicLessonCache } from "../lib/useTopicLesson";
import { QuizHistoryPage } from "./QuizHistoryPage";
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
        <Route path="/subjects/:subjectId/topics/:topicId/history" element={<QuizHistoryPage />} />
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
    expect(screen.getByRole("link", { name: "Back to topic" })).toHaveAttribute(
      "href",
      "/subjects/subj-1/topics/topic-1",
    );
    expect(screen.queryByRole("complementary", { name: "Topic quiz" })).not.toBeInTheDocument();
    expect(screen.queryByText(/correct_rationale/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/qa_items/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /answer key/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/Finish every slide to unlock/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Mathematics" })).toHaveAttribute(
      "href",
      "/subjects/subj-1",
    );
  });

  it("shows a mensuration-style topic lesson as slides and unlocks the quiz on the last slide", async () => {
    const formula = [
      "$$\\text{Required area} = (l + 2w)(b + 2w) - lb",
      "= 2400 + 3200 + (2 \\times 1200) = 8000 \\, \\text{cm}^2.$$",
    ].join("\n");
    const markdown = [
      "# Mensuration",
      "",
      "## Area of the path",
      "",
      "A path runs around a rectangular park.",
      "",
      "| Shape | Length | Breadth | Area |",
      "| --- | --- | --- | --- |",
      "| Park | 40 m | 30 m | 1200 |",
      "| Outer | 44 m | 34 m | 1496 |",
      "",
      formula,
      "",
      "### Check",
      "",
      "Subtract the park from the outer rectangle.",
    ].join("\n");
    getTopicMaterial.mockResolvedValue({
      ...lessonPayload,
      title: "Mensuration",
      markdown,
      quiz_unlocked: false,
    });
    let releaseProgress: (value: {
      status: string;
      last_unit_ordinal: number;
      opened_at: string;
      last_opened_at: string;
      completed_at: string | null;
      source_material_version_id: string;
    }) => void = () => undefined;
    updateTopicMaterialProgress.mockImplementation(
      (_topicId: string, body: { status: string; last_unit_ordinal: number }) =>
        new Promise((resolve) => {
          releaseProgress = resolve;
          if (body.status !== "completed") {
            resolve({
              status: body.status,
              last_unit_ordinal: body.last_unit_ordinal,
              opened_at: "2026-08-09T21:00:00Z",
              last_opened_at: "2026-08-09T21:00:00Z",
              completed_at: null,
              source_material_version_id: "ver-topic-1",
            });
          }
        }),
    );
    fetchLearningDirectory.mockResolvedValue({
      ...directory,
      subjects: [
        {
          ...directory.subjects[0],
          topics: [
            {
              ...directory.subjects[0].topics[0],
              title: "Mensuration",
              overall_quiz: {
                ...directory.subjects[0].topics[0].overall_quiz!,
                unlocked: false,
                locked_reason: "Complete the topic lesson first",
              },
            },
          ],
        },
      ],
    });

    renderTopicLesson();

    expect(await screen.findByRole("heading", { name: "Area of the path" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Mensuration" })).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "1200" })).toBeInTheDocument();
    expect(screen.getAllByText(/8000/).length).toBeGreaterThan(0);
    expect(document.querySelector(".katex")).not.toBeNull();
    expect(document.querySelector(".katex-error")).toBeNull();
    expect(screen.getByText("Complete the topic lesson first.")).toBeInTheDocument();
    expect(screen.queryByText(/unlocks when/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Start quiz" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Area of the path" })).toBeInTheDocument();
    expect(screen.getByText("Area of the path", { selector: ".slide-nav__section" })).toBeInTheDocument();
    expect(
      document.querySelector(".lesson-overview__scroll")?.contains(
        screen.getByRole("heading", { name: "Area of the path" }),
      ),
    ).toBe(false);
    expect(screen.getByRole("heading", { name: "Check" })).toBeInTheDocument();
    expect(screen.getByText(/Slide 1 of 1/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Next slide" })).not.toBeInTheDocument();

    await waitFor(() => {
      expect(updateTopicMaterialProgress).toHaveBeenCalledWith("topic-1", {
        status: "completed",
        last_unit_ordinal: 1,
      });
    });
    releaseProgress({
      status: "completed",
      last_unit_ordinal: 1,
      opened_at: "2026-08-09T21:00:00Z",
      last_opened_at: "2026-08-09T21:00:00Z",
      completed_at: "2026-08-09T21:10:00Z",
      source_material_version_id: "ver-topic-1",
    });
    expect(await screen.findByRole("link", { name: "Start quiz" })).toHaveAttribute(
      "href",
      "/quizzes/overall-1",
    );
    expect(screen.getByText("Topic quiz follows the slides.")).toBeInTheDocument();
  });

  it("opens the slide from the resume query", async () => {
    getTopicMaterial.mockResolvedValue({
      ...lessonPayload,
      title: "Mensuration",
      markdown: ["## Perimeter", "", "Walk the outside.", "", "## Area", "", "Cover the inside."].join(
        "\n",
      ),
      progress: {
        status: "opened",
        opened_at: "2026-08-09T21:00:00Z",
        last_opened_at: "2026-08-09T21:00:00Z",
        completed_at: null,
        last_unit_ordinal: 1,
        source_material_version_id: "ver-topic-1",
      },
    });

    renderTopicLesson("/subjects/subj-1/topics/topic-1/lesson?slide=2");

    expect(await screen.findByText("Area", { selector: ".slide-nav__section" })).toBeInTheDocument();
    expect(document.querySelector(".lesson-overview__scroll")).toHaveAttribute("data-slide-index", "1");
  });

  it("keeps Next slide enabled until the last slide", async () => {
    const user = userEvent.setup();
    getTopicMaterial.mockResolvedValue({
      ...lessonPayload,
      title: "Mensuration",
      markdown: ["## Perimeter", "", "Walk the outside.", "", "## Area", "", "Cover the inside."].join(
        "\n",
      ),
      quiz_unlocked: false,
    });
    renderTopicLesson();

    const next = await screen.findByRole("button", { name: "Next slide" });
    expect(next).toBeEnabled();
    expect(screen.getByText("Perimeter", { selector: ".slide-nav__section" })).toBeInTheDocument();
    expect(screen.getByText(/Slide 1 of 2/)).toBeInTheDocument();
    const scroller = document.querySelector(".lesson-overview__scroll");
    expect(scroller).toHaveAttribute("data-slide-index", "0");
    expect(scroller?.contains(next)).toBe(false);
    expect(scroller?.textContent).not.toMatch(/Slide 1 of 2/);

    await user.click(next);

    expect(screen.getByText("Area", { selector: ".slide-nav__section" })).toBeInTheDocument();
    expect(screen.getByText(/Slide 2 of 2/)).toBeInTheDocument();
    expect(document.querySelector(".lesson-overview__scroll")).toHaveAttribute("data-slide-index", "1");
    expect(screen.queryByRole("button", { name: "Next slide" })).not.toBeInTheDocument();
  });

  it("offers a completed lesson and its quiz as separate topic entries", async () => {
    getTopicMaterial.mockResolvedValue({
      ...lessonPayload,
      progress: {
        status: "completed",
        opened_at: "2026-08-09T21:00:00Z",
        last_opened_at: "2026-08-09T21:00:00Z",
        completed_at: "2026-08-09T21:10:00Z",
        last_unit_ordinal: 4,
        source_material_version_id: "ver-topic-1",
      },
    });
    fetchLearningDirectory.mockResolvedValue({
      ...directory,
      subjects: [
        {
          ...directory.subjects[0],
          topics: [
            {
              ...directory.subjects[0].topics[0],
              title: "Mensuration",
              topic_lesson_completed: true,
              progress_percent: 50,
              overall_quiz: {
                ...directory.subjects[0].topics[0].overall_quiz!,
                unlocked: true,
                attempt_count: 1,
                best_score_percent: 80,
                passed: true,
                recent_attempts: [
                  {
                    id: "att-1",
                    attempt_number: 1,
                    status: "submitted",
                    score_percent: 80,
                    passed: true,
                    started_at: "2026-08-09T22:00:00Z",
                    submitted_at: "2026-08-09T22:20:00Z",
                  },
                ],
              },
            },
          ],
        },
      ],
    });

    renderTopicLesson("/subjects/subj-1/topics/topic-1");

    expect(await screen.findByRole("heading", { name: "Mensuration" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Lesson/ })).toHaveAttribute(
      "href",
      "/subjects/subj-1/topics/topic-1/lesson",
    );
    expect(screen.getByText("Review")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Quiz/ })).toHaveAttribute(
      "href",
      "/quizzes/overall-1",
    );
    expect(screen.getByText("Retake")).toBeInTheDocument();
    expect(screen.getByText("Last score 80%")).toBeInTheDocument();
    expect(screen.queryByText("A rectangle has four right angles.")).not.toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: "Topic quiz" })).not.toBeInTheDocument();
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

  it("links the topic page to full quiz history", async () => {
    renderTopicLesson("/subjects/subj-1/topics/topic-1");

    expect(await screen.findByRole("link", { name: /Show full history/i })).toHaveAttribute(
      "href",
      "/subjects/subj-1/topics/topic-1/history",
    );
  });

  it("renders the topic history route with the chart and attempts", async () => {
    fetchLearningDirectory.mockResolvedValue({
      ...directory,
      subjects: [
        {
          ...directory.subjects[0],
          topics: [
            {
              ...directory.subjects[0].topics[0],
              title: "Decimals",
              overall_quiz: {
                ...directory.subjects[0].topics[0].overall_quiz!,
                attempt_count: 2,
                best_score_percent: 85,
                passed: true,
                recent_attempts: [
                  {
                    id: "later",
                    attempt_number: 2,
                    status: "submitted",
                    score_percent: 20,
                    passed: false,
                    started_at: "2026-08-10T10:00:00Z",
                    submitted_at: "2026-08-10T10:15:00Z",
                  },
                  {
                    id: "earlier",
                    attempt_number: 1,
                    status: "submitted",
                    score_percent: 85,
                    passed: true,
                    started_at: "2026-08-09T10:00:00Z",
                    submitted_at: "2026-08-09T10:15:00Z",
                  },
                ],
              },
            },
          ],
        },
      ],
    });

    renderTopicLesson("/subjects/subj-1/topics/topic-1/history");

    expect(await screen.findByRole("heading", { name: "Quiz history" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Performance over time" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "All attempts" })).toBeInTheDocument();
    expect(screen.getByText("Not passed · 20%")).toBeInTheDocument();
    expect(screen.getByText("Passed · 85%")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "View" }).map((link) => link.getAttribute("href"))).toEqual([
      "/attempts/later",
      "/attempts/earlier",
    ]);
    expect(screen.getByRole("link", { name: "Decimals" })).toHaveAttribute(
      "href",
      "/subjects/subj-1/topics/topic-1",
    );
  });
});
