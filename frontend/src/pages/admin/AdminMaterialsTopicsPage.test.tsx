import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { LearningDirectory } from "../../api/types";
import { AdminMaterialsTopicsPage } from "./AdminMaterialsTopicsPage";

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({
    isDevMockSession: false,
  }),
}));

vi.mock("../../api/materials", () => ({
  fetchLearningDirectory: vi.fn(),
}));

vi.mock("../../api/generation", () => ({
  listTopicGenerationRuns: vi.fn(),
  submitSubjectGenerationRun: vi.fn(),
  deleteUnpublishedTopic: vi.fn(),
  discardGenerationRun: vi.fn(),
  retryGenerationRun: vi.fn(),
  isInFlightGenerationPhase: (phase: string) =>
    phase === "indexing" ||
    phase === "outlining" ||
    phase === "outline_review" ||
    phase === "generating" ||
    phase === "qa_review",
}));

import {
  deleteUnpublishedTopic,
  discardGenerationRun,
  listTopicGenerationRuns,
  retryGenerationRun,
  submitSubjectGenerationRun,
} from "../../api/generation";
import { fetchLearningDirectory } from "../../api/materials";

const mockDirectory: LearningDirectory = {
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
          title: "Approved Materials",
          slug: "approved_materials",
          sequence: 1,
          progress_percent: 0,
          complete: false,
          objectives: [],
          has_topic_lesson: true,
          subtopics: [
            {
              id: "st-1-uuid",
              title: "Properties of Rectangles and Squares",
              slug: "rectangles_squares_properties",
              sequence: 1,
              has_lesson: true,
              lesson_completed: false,
              progress_percent: 0,
              quiz: null,
            },
            {
              id: "st-2-uuid",
              title: "Square Numbers and Odd Number Sums",
              slug: "square_numbers_patterns",
              sequence: 2,
              has_lesson: true,
              lesson_completed: false,
              progress_percent: 0,
              quiz: null,
            },
          ],
          overall_quiz: null,
        },
        {
          id: "topic-2-uuid",
          title: "Fractions",
          slug: "fractions",
          sequence: 2,
          progress_percent: 0,
          complete: false,
          objectives: [],
          has_topic_lesson: false,
          subtopics: [],
          overall_quiz: null,
        },
      ],
    },
  ],
};

function renderPage(path = "/admin/materials/grades/grade-8/subjects/subj-math-uuid") {
  return render(
    <MemoryRouter
      initialEntries={[path]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route
          path="/admin/materials/grades/:gradeKey/subjects/:subjectId"
          element={<AdminMaterialsTopicsPage />}
        />
        <Route
          path="/admin/materials/grades/:gradeKey/subjects/:subjectId/topics/:topicId"
          element={<p>Topic pipeline</p>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AdminMaterialsTopicsPage", () => {
  beforeEach(() => {
    vi.mocked(fetchLearningDirectory).mockReset();
    vi.mocked(fetchLearningDirectory).mockResolvedValue(mockDirectory);
    vi.mocked(listTopicGenerationRuns).mockReset();
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([]);
    vi.mocked(submitSubjectGenerationRun).mockReset();
    vi.mocked(deleteUnpublishedTopic).mockReset();
    vi.mocked(discardGenerationRun).mockReset();
    vi.mocked(retryGenerationRun).mockReset();
  });

  it("lists chapter cards instead of only the parent topic row", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: "Topics" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Units" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Subtopics" })).not.toBeInTheDocument();
    expect(screen.getByText("Properties of Rectangles and Squares")).toBeInTheDocument();
    expect(screen.getByText("Square Numbers and Odd Number Sums")).toBeInTheDocument();
    expect(screen.getByText("Fractions")).toBeInTheDocument();
    expect(screen.queryByText(/Unit 1/)).not.toBeInTheDocument();
    expect(screen.queryByText(/subtopic/i)).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Properties of Rectangles and Squares/i }),
    ).toHaveAttribute(
      "href",
      "/admin/materials/grades/grade-8/subjects/subj-math-uuid/topics/topic-1-uuid?tab=published&unit=st-1-uuid",
    );
    expect(screen.getByRole("link", { name: /Fractions/i })).toHaveAttribute(
      "href",
      "/admin/materials/grades/grade-8/subjects/subj-math-uuid/topics/topic-2-uuid",
    );
    expect(screen.getByRole("link", { name: "Open topic lesson" })).toHaveAttribute(
      "href",
      "/admin/materials/grades/grade-8/subjects/subj-math-uuid/topics/topic-1-uuid?tab=published",
    );
    expect(screen.getAllByText("published").length).toBeGreaterThan(0);
    expect(fetchLearningDirectory).toHaveBeenCalled();
  });

  it("uploads a topic PDF and navigates to the new topic", async () => {
    const user = userEvent.setup();
    vi.mocked(submitSubjectGenerationRun).mockResolvedValue({
      run_id: "run-new",
      topic_id: "topic-new-uuid",
      phase: "indexing",
    });

    renderPage();
    await user.click(await screen.findByRole("button", { name: "Upload" }));

    await user.type(screen.getByLabelText(/^title$/i), "Fractions extra");
    const file = new File(["%PDF-1.4"], "fractions.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText(/pdf file/i), { target: { files: [file] } });
    await user.click(screen.getByRole("button", { name: "Upload topic PDF" }));

    await waitFor(() => {
      expect(submitSubjectGenerationRun).toHaveBeenCalled();
    });
    const [subjectId, uploaded, title, targetItemCount, gradeName] = vi.mocked(
      submitSubjectGenerationRun,
    ).mock.calls[0]!;
    expect(subjectId).toBe("subj-math-uuid");
    expect(uploaded).toBeInstanceOf(File);
    expect(title).toBe("Fractions extra");
    expect(targetItemCount).toBeUndefined();
    expect(gradeName).toBe("Grade 8");
    expect(await screen.findByText("Topic pipeline")).toBeInTheDocument();
  });

  it("shows an in-progress badge when a topic has an in-flight run", async () => {
    vi.mocked(fetchLearningDirectory).mockResolvedValue({
      ...mockDirectory,
      subjects: [
        {
          ...mockDirectory.subjects[0]!,
          topics: [
            {
              ...mockDirectory.subjects[0]!.topics[1]!,
              has_topic_lesson: false,
            },
          ],
        },
      ],
    });
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([
      {
        id: "run-1",
        topic_id: "topic-2-uuid",
        title: "Fractions source",
        phase: "indexing",
        target_item_count: 80,
        submitted_by_user_id: "u1",
        intake_version_id: "v1",
        failure_reason: null,
        outline: null,
        draft_lesson_markdown: null,
        jobs: [],
        created_at: "2026-09-09T00:00:00Z",
      },
    ]);

    renderPage();
    expect(await screen.findByText("in progress")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    expect(screen.queryByText("published")).not.toBeInTheDocument();
    expect(screen.getByText("Fractions")).toBeInTheDocument();
    expect(screen.queryByText("Approved Materials")).not.toBeInTheDocument();
  });

  it("cancels an in-progress run and retries the same PDF", async () => {
    const user = userEvent.setup();
    vi.mocked(fetchLearningDirectory).mockResolvedValue({
      ...mockDirectory,
      subjects: [
        {
          ...mockDirectory.subjects[0]!,
          topics: [{ ...mockDirectory.subjects[0]!.topics[1]!, has_topic_lesson: false }],
        },
      ],
    });
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([
      {
        id: "run-1",
        topic_id: "topic-2-uuid",
        title: "Fractions source",
        phase: "outline_review",
        target_item_count: 80,
        submitted_by_user_id: "u1",
        intake_version_id: "v1",
        failure_reason: null,
        outline: null,
        draft_lesson_markdown: null,
        jobs: [],
        created_at: "2026-09-09T00:00:00Z",
      },
    ]);
    vi.mocked(discardGenerationRun).mockResolvedValue({
      id: "run-1",
      topic_id: "topic-2-uuid",
      title: "Fractions source",
      phase: "discarded",
      target_item_count: 80,
      submitted_by_user_id: "u1",
      intake_version_id: "v1",
      failure_reason: null,
      outline: null,
      draft_lesson_markdown: null,
      jobs: [],
      created_at: "2026-09-09T00:00:00Z",
    });
    vi.mocked(retryGenerationRun).mockResolvedValue({
      id: "run-1",
      topic_id: "topic-2-uuid",
      title: "Fractions source",
      phase: "outlining",
      target_item_count: 80,
      submitted_by_user_id: "u1",
      intake_version_id: "v1",
      failure_reason: null,
      outline: null,
      draft_lesson_markdown: null,
      jobs: [],
      created_at: "2026-09-09T00:00:00Z",
    });

    renderPage();
    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(discardGenerationRun).toHaveBeenCalledWith("run-1");
    await user.click(await screen.findByRole("button", { name: "Retry" }));
    expect(retryGenerationRun).toHaveBeenCalledWith("run-1");
    expect(await screen.findByText("in progress")).toBeInTheDocument();
  });

  it("removes an unpublished unit and leaves published units", async () => {
    const user = userEvent.setup();
    vi.mocked(deleteUnpublishedTopic).mockResolvedValue(undefined);

    renderPage();

    expect(await screen.findByText("Properties of Rectangles and Squares")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Remove" })).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Remove" }));
    await user.click(screen.getByRole("button", { name: "Confirm remove" }));

    expect(deleteUnpublishedTopic).toHaveBeenCalledWith("topic-2-uuid");
    await waitFor(() => {
      expect(screen.queryByText("Fractions")).not.toBeInTheDocument();
    });
    expect(screen.getByText("Properties of Rectangles and Squares")).toBeInTheDocument();
    expect(screen.getByText("Square Numbers and Odd Number Sums")).toBeInTheDocument();
  });
});
