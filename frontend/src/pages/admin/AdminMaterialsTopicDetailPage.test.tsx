import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { GenerationQaItem, GenerationRun, LearningDirectory } from "../../api/types";
import { AdminMaterialsTopicDetailPage } from "./AdminMaterialsTopicDetailPage";

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({
    isDevMockSession: false,
    hasRole: (role: string) => role === "administrator",
  }),
}));

vi.mock("../../api/materials", () => ({
  fetchLearningDirectory: vi.fn(),
  getTopicMaterial: vi.fn(),
  getSubtopicMaterial: vi.fn(),
}));

vi.mock("../../api/generation", () => ({
  listTopicGenerationRuns: vi.fn(),
  listGenerationSteps: vi.fn(async () => []),
  getGenerationStepDetail: vi.fn(),
  fetchTopicItemStats: vi.fn(),
  discardGenerationRun: vi.fn(),
  retryGenerationRun: vi.fn(),
  subscribeGenerationRun: vi.fn(),
  submitTopicGenerationRun: vi.fn(),
  getGenerationRun: vi.fn(),
  getGenerationReview: vi.fn(),
  submitReviewDecision: vi.fn(),
  closeReviewRound: vi.fn(),
  publishGenerationRun: vi.fn(),
  rejectGenerationItems: vi.fn(),
  isInFlightGenerationPhase: (phase: string) =>
    phase === "indexing" ||
    phase === "outlining" ||
    phase === "outline_review" ||
    phase === "generating" ||
    phase === "qa_review",
}));

import {
  fetchTopicItemStats,
  getGenerationReview,
  getGenerationRun,
  listGenerationSteps,
  listTopicGenerationRuns,
  publishGenerationRun,
} from "../../api/generation";
import { fetchLearningDirectory, getSubtopicMaterial, getTopicMaterial } from "../../api/materials";

const path =
  "/admin/materials/grades/grade-8/subjects/subj-math-uuid/topics/topic-1-uuid";

function directory(over: Partial<LearningDirectory["subjects"][number]["topics"][number]> = {}): LearningDirectory {
  return {
    subjects: [
      {
        id: "subj-math-uuid",
        code: "MATH",
        name: "Mathematics",
        grade_name: "Grade 8",
        academic_period_name: "2025-2026",
        progress_percent: 0,
        topics: [
          {
            id: "topic-1-uuid",
            title: "Numbers",
            slug: "numbers",
            sequence: 1,
            progress_percent: 0,
            complete: false,
            objectives: [],
            has_topic_lesson: false,
            subtopics: [
              {
                id: "st-1-uuid",
                title: "Place value",
                slug: "place-value",
                sequence: 1,
                has_lesson: false,
                lesson_completed: false,
                progress_percent: 0,
                quiz: null,
              },
            ],
            overall_quiz: null,
            ...over,
          },
        ],
      },
    ],
  };
}

function qaItem(over: Partial<GenerationQaItem> = {}): GenerationQaItem {
  return {
    question_id: "q-1",
    question_version_id: "qv-1",
    prompt: "What is 1/2 + 1/2?",
    options: { A: "1", B: "1/2" },
    correct_label: "A",
    correct_rationale: "Two halves make a whole.",
    distractor_rationales: { B: "That is only one half." },
    subtopic_id: "st-1",
    sequence: 1,
    ...over,
  };
}

function run(over: Partial<GenerationRun> = {}): GenerationRun {
  return {
    id: "run-1",
    topic_id: "topic-1-uuid",
    title: "Numbers source",
    phase: "qa_review",
    target_item_count: 80,
    submitted_by_user_id: "u1",
    intake_version_id: "v1",
    failure_reason: null,
    outline: null,
    draft_lesson_markdown: "# Lesson\n\nAdd the numerators.",
    qa_items: [qaItem()],
    jobs: [],
    created_at: "2026-09-09T00:00:00Z",
    ...over,
  };
}

function renderPage(initialPath = path) {
  return render(
    <MemoryRouter
      initialEntries={[initialPath]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route
          path="/admin/materials/grades/:gradeKey/subjects/:subjectId/topics/:topicId"
          element={<AdminMaterialsTopicDetailPage />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AdminMaterialsTopicDetailPage", () => {
  beforeEach(() => {
    vi.mocked(fetchLearningDirectory).mockReset();
    vi.mocked(getTopicMaterial).mockReset();
    vi.mocked(getSubtopicMaterial).mockReset();
    vi.mocked(listTopicGenerationRuns).mockReset();
    vi.mocked(listGenerationSteps).mockReset();
    vi.mocked(fetchTopicItemStats).mockReset();
    vi.mocked(getGenerationReview).mockReset();
    vi.mocked(getGenerationRun).mockReset();
    vi.mocked(publishGenerationRun).mockReset();
    vi.mocked(fetchLearningDirectory).mockResolvedValue(directory());
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([]);
    vi.mocked(listGenerationSteps).mockResolvedValue([]);
    vi.mocked(fetchTopicItemStats).mockResolvedValue({
      topic_id: "topic-1-uuid",
      quiz_version_id: null,
      minimum_n: 20,
      items: [],
    });
    vi.mocked(getGenerationReview).mockResolvedValue({
      run_id: "run-1",
      topic_id: "topic-1-uuid",
      phase: "qa_review",
      published_locked: false,
      viewer_is_closer: true,
      active_revision: {
        stage: "qa",
        id: "rev-qa",
        run_id: "run-1",
        number: 1,
        parent_id: null,
        origin: "initial_generation",
        snapshot_hash: "def",
        created_at: "2026-09-13T00:00:00Z",
        created_by: { display_name: "lesson", occurred_at: "2026-09-13T00:00:00Z" },
        snapshot: {
          rendered_lesson_markdown: "# Lesson",
          quiz_version_id: "quiz-1",
          lesson_sections: [],
          quiz_items: [],
        },
      },
      diff_from_parent: null,
      open_round: {
        id: "round-qa",
        run_id: "run-1",
        revision_id: "rev-qa",
        stage: "qa",
        number: 1,
        opened_at: "2026-09-13T00:00:00Z",
        due_at: "2026-09-16T00:00:00Z",
        sealed_at: null,
      },
      reviewer_states: [],
      request_threads: [],
      history: [],
    });
  });

  it("keeps breadcrumbs and uses Upload | Published tabs", async () => {
    renderPage();

    expect(await screen.findByRole("tab", { name: "Upload" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Published" })).toBeInTheDocument();
    expect(screen.getByRole("tablist", { name: "Topic content" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Lessons" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Quizzes" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Numbers" })).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Upload topic PDF" })).toBeInTheDocument();
    expect(screen.queryByText("Index-only subtopic PDF")).not.toBeInTheDocument();
    expect(screen.queryByText("No lesson yet")).not.toBeInTheDocument();
  });

  it("opens published lesson and quiz immediately without a catalog row", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchLearningDirectory).mockResolvedValue(
      directory({
        has_topic_lesson: true,
        overall_quiz: {
          id: "quiz-1-uuid",
          title: "Numbers mastery",
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
      }),
    );
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([
      run({
        phase: "published",
        published_lesson_version_id: "lesson-v1",
        published_quiz_version_id: "quiz-v1",
      }),
    ]);
    vi.mocked(getTopicMaterial).mockResolvedValue({
      id: "topic-1-uuid",
      title: "Numbers",
      markdown: "# Numbers\n\nA whole is two halves.",
      slides: [
        { number: 1, title: "Introduction", content: "A whole is two halves." },
        { number: 2, title: "Lesson Summary", content: "Two halves make one." },
      ],
      source_material_version_id: "ver-1",
      published_at: "2026-09-09T00:00:00Z",
      quiz_unlocked: true,
      quiz_id: "quiz-1-uuid",
      progress: null,
    });

    renderPage();

    expect(await screen.findByRole("tab", { name: "Published" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(document.querySelectorAll(".admin-published-row")).toHaveLength(0);
    expect(await screen.findByRole("heading", { name: "Introduction" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Numbers" })).toBeInTheDocument();
    expect(screen.getByText("Published 9 Sep 2026 · 2 slides · 1 question")).toBeInTheDocument();
    expect(screen.getByText("A whole is two halves.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Lesson Summary" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open lesson as student" })).toHaveAttribute(
      "href",
      "/subjects/subj-math-uuid/topics/topic-1-uuid/lesson",
    );
    expect(screen.getByRole("link", { name: "Open quiz as student" })).toHaveAttribute(
      "href",
      "/quizzes/quiz-1-uuid",
    );
    expect(await screen.findByText("What is 1/2 + 1/2?")).toBeInTheDocument();
    expect(screen.getByText("Correct")).toBeInTheDocument();
    expect(getTopicMaterial).toHaveBeenCalledWith("topic-1-uuid");
    expect(screen.queryByRole("link", { name: /Start quiz|Retry/i })).not.toBeInTheDocument();
    expect(screen.queryByText("Last attempt")).not.toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Published quiz" })).toHaveClass(
      "lesson-layout__aside",
    );
    expect(await screen.findByRole("region", { name: "Item statistics" })).toBeInTheDocument();
    expect(screen.getByText(/Not enough responses to flag items yet/)).toBeInTheDocument();

    expect(screen.getByRole("button", { name: "Next slide" })).toBeEnabled();
    expect(screen.getByText("Slide 1 of 2")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next slide" }));
    expect(await screen.findByRole("heading", { name: "Lesson Summary" })).toBeInTheDocument();
    expect(screen.getByText("Two halves make one.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next slide" })).toBeDisabled();
    expect(screen.queryByRole("link", { name: /^Start quiz$/i })).not.toBeInTheDocument();
  });

  it("switches to Published after a successful publish", async () => {
    const user = userEvent.setup();
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([run()]);
    vi.mocked(publishGenerationRun).mockResolvedValue({
      run_id: "run-1",
      topic_id: "topic-1-uuid",
      lesson_version_id: "lesson-v1",
      quiz_version_id: "quiz-v1",
      item_count: 1,
    });
    vi.mocked(getGenerationRun).mockResolvedValue(
      run({
        phase: "published",
        published_lesson_version_id: "lesson-v1",
        published_quiz_version_id: "quiz-v1",
      }),
    );
    vi.mocked(getTopicMaterial).mockResolvedValue({
      id: "topic-1-uuid",
      title: "Numbers",
      markdown: "# Numbers\n\nA whole is two halves.",
      slides: [
        { number: 1, title: "Introduction", content: "A whole is two halves." },
        { number: 2, title: "Lesson Summary", content: "Two halves make one." },
      ],
      source_material_version_id: "ver-1",
      quiz_unlocked: true,
      quiz_id: "quiz-1-uuid",
      progress: null,
    });
    vi.mocked(fetchLearningDirectory)
      .mockResolvedValueOnce(directory())
      .mockResolvedValueOnce(
        directory({
          has_topic_lesson: true,
          overall_quiz: {
            id: "quiz-1-uuid",
            title: "Numbers mastery",
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
        }),
      );

    renderPage();
    await user.click(await screen.findByRole("button", { name: "Publish to students" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Publish to students" }));

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Published" })).toHaveAttribute("aria-selected", "true");
    });
    expect(document.querySelectorAll(".admin-published-row")).toHaveLength(0);
    expect(await screen.findByRole("heading", { name: "Introduction" })).toBeInTheDocument();
    expect(screen.getByText("A whole is two halves.")).toBeInTheDocument();
    expect(fetchLearningDirectory).toHaveBeenCalledTimes(2);
  });

  it("still shows slides immediately when only the lesson is published", async () => {
    vi.mocked(fetchLearningDirectory).mockResolvedValue(directory({ has_topic_lesson: true }));
    vi.mocked(getTopicMaterial).mockResolvedValue({
      id: "topic-1-uuid",
      title: "Numbers",
      markdown: "## Fractions\n\nA part of a whole.",
      slides: [],
      source_material_version_id: "ver-1",
      quiz_unlocked: false,
      quiz_id: null,
      progress: null,
    });

    renderPage();

    expect(await screen.findByRole("tab", { name: "Published" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(document.querySelectorAll(".admin-published-row")).toHaveLength(0);
    expect(await screen.findByRole("heading", { name: "Fractions" })).toBeInTheDocument();
    expect(screen.getByText("A part of a whole.")).toBeInTheDocument();
    expect(screen.getByText("Slide 1 of 1")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next slide" })).toBeDisabled();
    expect(screen.getByText("No published quiz items on the latest run.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open quiz as student" })).not.toBeInTheDocument();
  });

  it("keeps the Upload tab as a blank form plus history when a published run exists", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchLearningDirectory).mockResolvedValue(directory({ has_topic_lesson: true }));
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([
      run({
        phase: "published",
        published_lesson_version_id: "lesson-v1",
        published_quiz_version_id: "quiz-v1",
      }),
    ]);
    vi.mocked(getTopicMaterial).mockResolvedValue({
      id: "topic-1-uuid",
      title: "Numbers",
      markdown: "## Fractions\n\nA part of a whole.",
      slides: [],
      source_material_version_id: "ver-1",
      quiz_unlocked: false,
      quiz_id: null,
      progress: null,
    });

    renderPage();
    expect(await screen.findByRole("tab", { name: "Published" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await user.click(screen.getByRole("tab", { name: "Upload" }));
    expect(await screen.findByRole("button", { name: "Upload topic PDF" })).toBeDisabled();
    expect(screen.getByRole("heading", { name: "Previous runs" })).toBeInTheDocument();
    expect(screen.queryByText("published. This run is published.")).not.toBeInTheDocument();
    expect(screen.queryByText("What is 1/2 + 1/2?")).not.toBeInTheDocument();
  });

  it("shows a recap list, nested lists, a table, and filters quiz cards", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchLearningDirectory).mockResolvedValue(directory({ has_topic_lesson: true }));
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([
      run({
        phase: "published",
        qa_items: [
          qaItem({
            question_id: "q-easy",
            prompt: "What is the area of a square?",
            difficulty: "easy",
            subtopic_title: "Area of a polygon",
          }),
          qaItem({
            question_id: "q-hard",
            question_version_id: "qv-2",
            sequence: 2,
            prompt: "What is the volume of a cube?",
            difficulty: "hard",
            subtopic_title: "Volume of a cube",
          }),
        ],
      }),
    ]);
    vi.mocked(getTopicMaterial).mockResolvedValue({
      id: "topic-1-uuid",
      title: "Mensuration",
      markdown: "",
      slides: [
        {
          number: 1,
          title: "Area of a polygon",
          content: [
            "1. Surface area is the total of the faces.",
            "   - A cuboid uses $2(lb + bh + hl)$.",
            "   - A cube uses $6a^2$.",
            "",
            "| Shape | Area |",
            "| --- | --- |",
            "| Square | $a^2$ |",
            "",
            "$$\\text{Area} = l \\times b$$",
            "",
            "**Topic recap.**",
            "",
            "- Area of a polygon",
            "- Volume of a cube",
          ].join("\n"),
        },
      ],
      source_material_version_id: "ver-1",
      quiz_unlocked: true,
      quiz_id: "quiz-1-uuid",
      progress: null,
    });

    renderPage();

    const surface = await screen.findByText(/Surface area is the total/);
    const item = surface.closest("li");
    expect(item?.querySelector("ul")).not.toBeNull();
    const scroller = document.querySelector(".lesson-overview__scroll");
    expect(scroller?.querySelector("table")).not.toBeNull();
    expect(scroller?.querySelector(".katex-display")).not.toBeNull();
    expect(scroller?.textContent).toContain("Area of a polygon");
    expect(scroller?.textContent).toContain("Volume of a cube");
    expect(screen.queryByText(/TRY THESE/)).not.toBeInTheDocument();
    expect(screen.getByText("2 questions · 1 easy · 1 hard")).toBeInTheDocument();
    expect(screen.getByText("What is the area of a square?")).toBeInTheDocument();
    expect(screen.getByText("What is the volume of a cube?")).toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: "Filter by difficulty" }), "hard");
    expect(screen.queryByText("What is the area of a square?")).not.toBeInTheDocument();
    expect(screen.getByText("What is the volume of a cube?")).toBeInTheDocument();
  });

  it("opens a chapter's published lesson from tab and unit query params", async () => {
    vi.mocked(fetchLearningDirectory).mockResolvedValue(
      directory({
        has_topic_lesson: false,
        subtopics: [
          {
            id: "st-1-uuid",
            title: "Place value",
            slug: "place-value",
            sequence: 1,
            has_lesson: true,
            lesson_completed: false,
            progress_percent: 0,
            quiz: null,
          },
        ],
      }),
    );
    vi.mocked(getSubtopicMaterial).mockResolvedValue({
      id: "st-1-uuid",
      title: "Place value",
      markdown: "## Place value\n\nTens and ones.",
      slides: [{ number: 1, title: "Tens and ones", content: "Tens sit to the left of ones." }],
      source_material_version_id: "ver-st-1",
      quiz_unlocked: true,
      quiz_id: "quiz-st-1",
      progress: null,
    });

    renderPage(`${path}?tab=published&unit=st-1-uuid`);

    expect(await screen.findByRole("tab", { name: "Published" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(await screen.findByRole("heading", { name: "Tens and ones" })).toBeInTheDocument();
    expect(screen.getByText("Tens sit to the left of ones.")).toBeInTheDocument();
    expect(getSubtopicMaterial).toHaveBeenCalledWith("st-1-uuid");
    expect(getTopicMaterial).not.toHaveBeenCalled();
  });
});
