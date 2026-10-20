import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  GenerationOutlineNode,
  GenerationQaItem,
  GenerationRun,
  GenerationStepTiming,
} from "../api/types";
import { TopicGenerationUpload } from "./TopicGenerationUpload";

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    isDevMockSession: false,
    hasRole: (role: string) => role === "administrator",
  }),
}));

vi.mock("../api/generation", () => ({
  listTopicGenerationRuns: vi.fn(),
  listGenerationSteps: vi.fn(async () => []),
  getGenerationStepDetail: vi.fn(),
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
  closeReviewRound,
  discardGenerationRun,
  getGenerationReview,
  getGenerationRun,
  getGenerationStepDetail,
  listGenerationSteps,
  listTopicGenerationRuns,
  subscribeGenerationRun,
  publishGenerationRun,
  rejectGenerationItems,
  retryGenerationRun,
} from "../api/generation";
import type { ReviewWorkspace } from "../api/types";

function node(over: Partial<GenerationOutlineNode> = {}): GenerationOutlineNode {
  return {
    id: "node-1",
    parent_id: null,
    slug: "fractions",
    title: "Fractions",
    token_mass: 120,
    prerequisite_score: "0.10",
    centrality: "0.40",
    weight: "0.5000",
    quota: 40,
    matched_subtopic_id: null,
    force_create: false,
    accepted_subtopic_id: null,
    proposed_outcomes: ["Add like fractions"],
    sequence: 1,
    ...over,
  };
}

function qaItem(over: Partial<GenerationQaItem> = {}): GenerationQaItem {
  return {
    question_id: "q-1",
    question_version_id: "qv-1",
    prompt: "What is 1/2 + 1/2?",
    options: { A: "1", B: "1/2", C: "2", D: "0" },
    correct_label: "A",
    correct_rationale: "Two halves make a whole.",
    distractor_rationales: { B: "That is only one half.", C: "Too large.", D: "Too small." },
    subtopic_id: "st-1",
    sequence: 1,
    ...over,
  };
}

function run(over: Partial<GenerationRun> = {}): GenerationRun {
  return {
    id: "run-1",
    topic_id: "topic-1",
    title: "Unit source",
    phase: "outline_review",
    target_item_count: 80,
    submitted_by_user_id: "u1",
    intake_version_id: "v1",
    failure_reason: null,
    outline: {
      target_item_count: 80,
      nodes: [node()],
    },
    draft_lesson_markdown: null,
    jobs: [],
    created_at: "2026-09-09T00:00:00Z",
    ...over,
  };
}

function workspace(over: Partial<ReviewWorkspace> = {}): ReviewWorkspace {
  return {
    run_id: "run-1",
    topic_id: "topic-1",
    phase: "outline_review",
    published_locked: false,
    viewer_is_closer: true,
    active_revision: {
      stage: "outline",
      id: "rev-1",
      run_id: "run-1",
      number: 1,
      parent_id: null,
      origin: "initial_generation",
      snapshot_hash: "abc",
      created_at: "2026-09-13T00:00:00Z",
      created_by: { display_name: "outline", occurred_at: "2026-09-13T00:00:00Z" },
      snapshot: {
        target_item_count: 80,
        nodes: [
          {
            node_key: "node-1",
            parent_node_key: null,
            slug: "fractions",
            title: "Fractions",
            token_mass: 120,
            prerequisite_score: "0.10",
            centrality: "0.40",
            weight: "0.5000",
            matched_subtopic_id: null,
            force_create: false,
            proposed_outcomes: ["Add like fractions"],
            sequence: 1,
            quota: 12,
          },
        ],
      },
    },
    diff_from_parent: null,
    open_round: {
      id: "round-1",
      run_id: "run-1",
      revision_id: "rev-1",
      stage: "outline",
      number: 1,
      opened_at: "2026-09-13T00:00:00Z",
      due_at: "2026-09-16T00:00:00Z",
      sealed_at: null,
    },
    reviewer_states: [],
    request_threads: [],
    history: [],
    ...over,
  };
}

function outlineSnapshot(ws: ReviewWorkspace = workspace()) {
  const revision = ws.active_revision;
  if (revision.stage !== "outline") {
    throw new Error("expected an outline revision");
  }
  return revision.snapshot;
}

function qaWorkspace(over: Partial<ReviewWorkspace> = {}): ReviewWorkspace {
  return workspace({
    phase: "qa_review",
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
        lesson_sections: [
          {
            section_key: "sec-1",
            heading: "Like fractions",
            markdown: "**The idea.** Add the numerators.",
            sequence: 1,
          },
        ],
        quiz_items: [
          {
            item_key: "item-1",
            question_id: "q-1",
            question_version_id: "qv-1",
            subtopic_id: "st-1",
            prompt: "What is 1/2 + 1/2?",
            options: { A: "1", B: "2" },
            correct_label: "A",
            correct_rationale: "Two halves make a whole.",
            distractor_rationales: { B: "Too large." },
            sequence: 1,
          },
        ],
      },
    },
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
    ...over,
  });
}

function renderPanel() {
  return render(<TopicGenerationUpload topicId="topic-1" defaultTitle="Unit source" />);
}

describe("TopicGenerationUpload", () => {
  beforeEach(() => {
    vi.mocked(listTopicGenerationRuns).mockReset();
    vi.mocked(listGenerationSteps).mockReset();
    vi.mocked(getGenerationStepDetail).mockReset();
    vi.mocked(discardGenerationRun).mockReset();
    vi.mocked(retryGenerationRun).mockReset();
    vi.mocked(subscribeGenerationRun).mockReset();
    vi.mocked(getGenerationRun).mockReset();
    vi.mocked(getGenerationReview).mockReset();
    vi.mocked(closeReviewRound).mockReset();
    vi.mocked(publishGenerationRun).mockReset();
    vi.mocked(rejectGenerationItems).mockReset();
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([]);
    vi.mocked(listGenerationSteps).mockResolvedValue([]);
    vi.mocked(subscribeGenerationRun).mockImplementation(() => new Promise(() => undefined));
    vi.mocked(getGenerationReview).mockResolvedValue(workspace());
  });

  it("loads the topic run on mount and renders node titles", async () => {
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([run()]);
    vi.mocked(getGenerationReview).mockResolvedValue(
      workspace({
        active_revision: {
          ...workspace().active_revision,
          stage: "outline",
          snapshot: {
            target_item_count: 80,
            nodes: [
              outlineSnapshot().nodes[0]!,
              {
                ...outlineSnapshot().nodes[0]!,
                node_key: "node-2",
                slug: "decimals",
                title: "Decimals",
                sequence: 2,
              },
            ],
          },
        },
      }),
    );

    renderPanel();

    expect(await screen.findByText("Sequence 1: Fractions")).toBeInTheDocument();
    expect(screen.getByText("Sequence 2: Decimals")).toBeInTheDocument();
    expect(listTopicGenerationRuns).toHaveBeenCalledWith("topic-1");
    expect(getGenerationReview).toHaveBeenCalledWith("run-1");
    expect(subscribeGenerationRun).toHaveBeenCalledWith(
      "run-1",
      expect.objectContaining({ signal: expect.any(AbortSignal), onSnapshot: expect.any(Function) }),
    );
  });

  it("confirms accept current without live outline PATCH", async () => {
    const user = userEvent.setup();
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([run()]);
    vi.mocked(closeReviewRound).mockResolvedValue({
      kind: "outline_accepted",
      run_id: "run-1",
    });
    vi.mocked(getGenerationRun).mockResolvedValue(
      run({
        phase: "qa_review",
        draft_lesson_markdown: "# Lesson",
        qa_items: [qaItem()],
        jobs: [],
      }),
    );

    renderPanel();
    await screen.findByText("Sequence 1: Fractions");
    await user.click(screen.getByRole("button", { name: "Accept current outline" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent(
      "This freezes subtopics and quotas and queues item and lesson generation.",
    );
    await user.click(within(dialog).getByRole("button", { name: "Accept current outline" }));

    expect(closeReviewRound).toHaveBeenCalledWith("run-1", "round-1", {
      revision_id: "rev-1",
      action: "accept_current",
      snapshot_hash: "abc",
    });
    expect(
      await screen.findByText("qa_review. Draft lesson and item bank are ready for QA."),
    ).toBeInTheDocument();
    expect(screen.getByText("What is 1/2 + 1/2?")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Lesson/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish to students" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save outline" })).not.toBeInTheDocument();
    expect(getGenerationRun).toHaveBeenCalledWith("run-1");
  });

  it("confirms override and accept with the frozen snapshot", async () => {
    const user = userEvent.setup();
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([run()]);
    vi.mocked(closeReviewRound).mockResolvedValue({
      kind: "outline_accepted",
      run_id: "run-1",
    });
    vi.mocked(getGenerationRun).mockResolvedValue(
      run({
        phase: "qa_review",
        draft_lesson_markdown: "# Lesson",
        qa_items: [qaItem()],
        jobs: [],
      }),
    );

    renderPanel();
    await screen.findByText("Sequence 1: Fractions");
    await user.type(screen.getByLabelText("Override rationale"), "Keep the current tree after round 2.");
    await user.click(screen.getByRole("button", { name: "Override and accept" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Override and accept" }));

    expect(closeReviewRound).toHaveBeenCalledWith("run-1", "round-1", {
      revision_id: "rev-1",
      action: "override_and_accept",
      snapshot_hash: "abc",
      rationale: "Keep the current tree after round 2.",
      replacement: outlineSnapshot(),
    });
    expect(
      await screen.findByText("qa_review. Draft lesson and item bank are ready for QA."),
    ).toBeInTheDocument();
  });

  it("confirms discard and posts discard", async () => {
    const user = userEvent.setup();
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([run()]);
    vi.mocked(discardGenerationRun).mockResolvedValue(run({ phase: "discarded", outline: null }));

    renderPanel();
    await screen.findByText("Sequence 1: Fractions");
    await user.click(screen.getByRole("button", { name: "Discard run" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Discard run" }));

    expect(discardGenerationRun).toHaveBeenCalledWith("run-1");
    expect(await screen.findByText(/This run was discarded/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save outline" })).not.toBeInTheDocument();
  });

  it("shows retry when the run failed and intake exists", async () => {
    const user = userEvent.setup();
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([
      run({
        phase: "failed",
        failure_reason: "Outline writer timed out",
        outline: null,
      }),
    ]);

    renderPanel();

    expect(await screen.findByRole("heading", { name: "Previous runs" })).toBeInTheDocument();
    expect(screen.queryByText(/Outline writer timed out/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Unit source/i }));
    expect(await screen.findByText(/Outline writer timed out/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save outline" })).not.toBeInTheDocument();
  });

  it("retries a discarded run from the same PDF", async () => {
    const user = userEvent.setup();
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([
      run({ phase: "discarded", outline: null, failure_reason: null }),
    ]);
    vi.mocked(retryGenerationRun).mockResolvedValue(run({ phase: "outlining", outline: null }));

    renderPanel();
    await user.click(await screen.findByRole("button", { name: /Unit source/i }));
    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(retryGenerationRun).toHaveBeenCalledWith("run-1");
    expect(await screen.findByText(/Building the outline/)).toBeInTheDocument();
  });

  it("shows parked copy after accept and hides the editor", async () => {
    let settle!: (value: GenerationRun) => void;
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([
      run({
        phase: "generating",
        jobs: [
          { kind: "items", status: "queued" },
          { kind: "lesson", status: "queued" },
        ],
      }),
    ]);
    vi.mocked(subscribeGenerationRun).mockImplementation((_runId, options) => {
      return new Promise((resolve) => {
        settle = (value) => {
          options?.onSnapshot?.(value);
          resolve(value);
        };
      });
    });

    renderPanel();

    expect(
      await screen.findByText(
        "generating. Outline accepted. Lesson generation is in progress. Quiz items start after the lesson.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("listitem", { current: "step" })).toHaveTextContent(
      "Generating lesson",
    );
    expect(screen.queryByRole("button", { name: "Save outline" })).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("Fractions")).not.toBeInTheDocument();
    expect(subscribeGenerationRun).toHaveBeenCalledWith(
      "run-1",
      expect.objectContaining({ signal: expect.any(AbortSignal), onSnapshot: expect.any(Function) }),
    );

    settle(
      run({
        phase: "qa_review",
        draft_lesson_markdown: "# Lesson",
        qa_items: [qaItem()],
        jobs: [],
      }),
    );
    expect(await screen.findByText("What is 1/2 + 1/2?")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Lesson/ })).toBeInTheDocument();
  });

  it("does not wedge upload on Processing when generating has no jobs", async () => {
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([
      run({ phase: "generating", jobs: [] }),
    ]);

    renderPanel();

    expect(
      await screen.findByText(
        "generating. Outline accepted. Queueing lesson generation. Quiz items start after the lesson.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("listitem", { current: "step" })).toHaveTextContent(
      "Generating lesson",
    );
    expect(screen.getByRole("button", { name: "Upload topic PDF" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Processing…" })).not.toBeInTheDocument();
  });

  it("shows lesson progress after items leave the in-flight job list", async () => {
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([
      run({ phase: "generating", jobs: [{ kind: "lesson", status: "running" }] }),
    ]);

    renderPanel();

    expect(
      await screen.findByText(
        "generating. Outline accepted. Lesson generation is in progress. Quiz items start after the lesson.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("listitem", { current: "step" })).toHaveTextContent("Generating lesson");
  });

  it("shows qa-review copy and a publish action", async () => {
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([
      run({
        phase: "qa_review",
        jobs: [],
        draft_lesson_markdown: "# Lesson",
        qa_items: [qaItem()],
      }),
    ]);
    vi.mocked(getGenerationReview).mockResolvedValue(qaWorkspace());

    renderPanel();

    expect(
      await screen.findByText("qa_review. Draft lesson and item bank are ready for QA."),
    ).toBeInTheDocument();
    expect(screen.getByRole("listitem", { current: "step" })).toHaveTextContent("Ready to review");
    expect(screen.getByText("Section 1: Like fractions")).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Lesson" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Quiz" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Lesson/ })).toBeInTheDocument();
    expect(screen.getAllByText("What is 1/2 + 1/2?").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Two halves make a whole.").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Publish to students" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject selected items" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save outline" })).not.toBeInTheDocument();
  });

  it("keeps polling while indexing until the outline is ready", async () => {
    let settle!: (value: GenerationRun) => void;
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([
      run({ phase: "indexing", outline: null }),
    ]);
    vi.mocked(subscribeGenerationRun).mockImplementation((_runId, options) => {
      return new Promise((resolve) => {
        settle = (value) => {
          options?.onSnapshot?.(value);
          resolve(value);
        };
      });
    });

    renderPanel();

    expect(await screen.findByText(/Indexing the uploaded PDF/)).toBeInTheDocument();
    expect(screen.getByRole("listitem", { current: "step" })).toHaveTextContent("Indexing PDF");
    expect(screen.queryByRole("button", { name: "Save outline" })).not.toBeInTheDocument();
    expect(subscribeGenerationRun).toHaveBeenCalledWith(
      "run-1",
      expect.objectContaining({ signal: expect.any(AbortSignal), onSnapshot: expect.any(Function) }),
    );

    settle(
      run({
        phase: "outline_review",
        outline: { target_item_count: 80, nodes: [node()] },
      }),
    );

    await waitFor(() => expect(getGenerationReview).toHaveBeenCalledWith("run-1"));
    expect(
      await screen.findByText("Sequence 1: Fractions", {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept current outline" })).toBeInTheDocument();
  });

  it("ticks the stepper as subscribeGenerationRun reports intermediate phases", async () => {
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([
      run({ phase: "indexing", outline: null }),
    ]);
    vi.mocked(subscribeGenerationRun).mockImplementation(async (_runId, options) => {
      options?.onSnapshot?.(run({ phase: "outlining", outline: null }));
      const ready = run({
        phase: "outline_review",
        outline: { target_item_count: 80, nodes: [node()] },
      });
      options?.onSnapshot?.(ready);
      return ready;
    });

    renderPanel();

    expect(await screen.findByText("Sequence 1: Fractions")).toBeInTheDocument();
    expect(screen.getByRole("listitem", { current: "step" })).toHaveTextContent("Outline review");
    expect(subscribeGenerationRun).toHaveBeenCalledWith(
      "run-1",
      expect.objectContaining({ onSnapshot: expect.any(Function) }),
    );
  });

  it("ticks generating steps from subscribeGenerationRun onSnapshot", async () => {
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([
      run({
        phase: "generating",
        jobs: [{ kind: "items", status: "running" }],
      }),
    ]);
    vi.mocked(getGenerationReview).mockResolvedValue(qaWorkspace());
    vi.mocked(subscribeGenerationRun).mockImplementation(async (_runId, options) => {
      options?.onSnapshot?.(
        run({ phase: "generating", jobs: [{ kind: "lesson", status: "running" }] }),
      );
      const ready = run({
        phase: "qa_review",
        draft_lesson_markdown: "# Lesson",
        qa_items: [qaItem()],
        jobs: [],
      });
      options?.onSnapshot?.(ready);
      return ready;
    });

    renderPanel();

    expect(await screen.findByText("qa_review. Draft lesson and item bank are ready for QA.")).toBeInTheDocument();
    expect(screen.getByRole("listitem", { current: "step" })).toHaveTextContent("Ready to review");
  });

  it("marks a failed lesson on the lesson step", async () => {
    const user = userEvent.setup();
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([
      run({
        phase: "failed",
        failure_reason: "Context variable not found: heading",
        jobs: [{ kind: "lesson", status: "failed" }],
      }),
    ]);

    renderPanel();
    await user.click(await screen.findByRole("button", { name: /Unit source/i }));

    expect(screen.getByText("Generating lesson").closest("li")).toHaveTextContent("Failed");
    expect(screen.getByText("Outline review").closest("li")).toHaveTextContent("Complete");
  });

  it("retries a failed run", async () => {
    const user = userEvent.setup();
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([
      run({ phase: "failed", failure_reason: "Outline writer timed out", outline: null }),
    ]);
    vi.mocked(retryGenerationRun).mockResolvedValue(run({ phase: "outlining", outline: null }));
    vi.mocked(subscribeGenerationRun).mockImplementation(async (_runId, options) => {
      const next = run({
        phase: "outline_review",
        outline: { target_item_count: 80, nodes: [node()] },
      });
      options?.onSnapshot?.(next);
      return next;
    });

    renderPanel();
    await user.click(await screen.findByRole("button", { name: /Unit source/i }));
    expect(screen.getByRole("alert")).toHaveTextContent("Outline writer timed out");
    await user.click(await screen.findByRole("button", { name: "Retry" }));

    expect(retryGenerationRun).toHaveBeenCalledWith("run-1");
    expect(await screen.findByText("Sequence 1: Fractions")).toBeInTheDocument();
  });

  it("confirms publish and shows the published state", async () => {
    const user = userEvent.setup();
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([
      run({
        phase: "qa_review",
        jobs: [],
        draft_lesson_markdown: "# Lesson",
        qa_items: [qaItem()],
      }),
    ]);
    vi.mocked(getGenerationReview).mockResolvedValue(qaWorkspace());
    vi.mocked(publishGenerationRun).mockResolvedValue({
      run_id: "run-1",
      topic_id: "topic-1",
      lesson_version_id: "lesson-v1",
      quiz_version_id: "quiz-v1",
      item_count: 1,
    });
    vi.mocked(getGenerationRun).mockResolvedValue(
      run({
        phase: "published",
        draft_lesson_markdown: "# Lesson",
        qa_items: [qaItem()],
        published_lesson_version_id: "lesson-v1",
        published_quiz_version_id: "quiz-v1",
      }),
    );

    renderPanel();
    await user.click(await screen.findByRole("button", { name: "Publish to students" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("This copies the draft lesson to students");
    await user.click(within(dialog).getByRole("button", { name: "Publish to students" }));

    expect(publishGenerationRun).toHaveBeenCalledWith("run-1");
    expect(await screen.findByText("published. This run is published.")).toBeInTheDocument();
    expect(screen.getByText(/Published lesson lesson-v1/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Publish to students" })).not.toBeInTheDocument();
  });

  it("hides bulk item reject in favor of section and item change requests", async () => {
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([
      run({
        phase: "qa_review",
        jobs: [],
        draft_lesson_markdown: "# Lesson",
        qa_items: [
          qaItem(),
          qaItem({ question_id: "q-2", sequence: 2, prompt: "Second stem" }),
        ],
      }),
    ]);
    vi.mocked(getGenerationReview).mockResolvedValue(qaWorkspace());

    renderPanel();

    expect(await screen.findByText("Section 1: Like fractions")).toBeInTheDocument();
    expect(screen.getByText("Second stem")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject selected items" })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Select item 2" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish to students" })).toBeInTheDocument();
    expect(rejectGenerationItems).not.toHaveBeenCalled();
  });

  it("hides the PDF form when upload is disabled", async () => {
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([run()]);

    render(<TopicGenerationUpload topicId="topic-1" defaultTitle="Unit source" allowUpload={false} />);

    expect(await screen.findByText("Sequence 1: Fractions")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept current outline" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save outline" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("PDF file")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Upload topic PDF" })).not.toBeInTheDocument();
  });

  it("does not auto-open a published run and opens it read-only from history", async () => {
    const user = userEvent.setup();
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([
      run({
        phase: "published",
        draft_lesson_markdown: "# Lesson",
        qa_items: [qaItem()],
        published_lesson_version_id: "lesson-v1",
        published_quiz_version_id: "quiz-v1",
      }),
    ]);

    renderPanel();

    expect(await screen.findByRole("button", { name: "Upload topic PDF" })).toBeDisabled();
    expect(screen.getByRole("heading", { name: "Previous runs" })).toBeInTheDocument();
    expect(screen.queryByText("published. This run is published.")).not.toBeInTheDocument();
    expect(screen.queryByText("What is 1/2 + 1/2?")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Unit source/i }));
    expect(await screen.findByText("published. This run is published.")).toBeInTheDocument();
    expect(screen.getByText("What is 1/2 + 1/2?")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Publish to students" })).not.toBeInTheDocument();
  });

  it("opens a completed step and disables a step that has not run", async () => {
    const user = userEvent.setup();
    const indexing: GenerationStepTiming = {
      step: "indexing",
      label: "Indexing PDF",
      status: "current",
      started_at: "2026-10-01T00:00:00Z",
      ended_at: "2026-10-01T00:01:00Z",
      duration_seconds: 60,
      error: null,
      available: true,
      hint: null,
    };
    vi.mocked(listTopicGenerationRuns).mockResolvedValue([
      run({ phase: "indexing", outline: null }),
    ]);
    vi.mocked(listGenerationSteps).mockResolvedValue([
      indexing,
      {
        ...indexing,
        step: "lesson",
        label: "Generating lesson",
        status: "pending",
        started_at: null,
        ended_at: null,
        duration_seconds: null,
        available: false,
        hint: "This step has not run yet.",
      },
    ]);
    vi.mocked(getGenerationStepDetail).mockResolvedValue({
      timing: { ...indexing, status: "complete" },
      indexing: {
        page_count: 4,
        chunk_count: 1,
        truncated: false,
        chunks: [
          {
            ordinal: 1,
            page_number: 3,
            section_heading: "Area of a polygon",
            preview: "A polygon is a closed plane figure.",
          },
        ],
      },
      outline: null,
      outline_review: null,
      lesson: null,
      items: null,
      ready: null,
    });

    renderPanel();

    const lesson = await screen.findByRole("button", { name: /Generating lesson/ });
    expect(lesson).toBeDisabled();
    expect(lesson).toHaveTextContent(/not run yet/i);
    await user.click(screen.getByRole("button", { name: /Indexing PDF/ }));

    expect(await screen.findByRole("region", { name: "Indexing PDF" })).toBeInTheDocument();
    expect(screen.getByText("A polygon is a closed plane figure.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Indexing PDF/ })).toHaveAttribute("aria-pressed", "true");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("region", { name: "Indexing PDF" })).not.toBeInTheDocument();
  });
});
