import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./client", () => ({
  apiRequest: vi.fn(),
}));

import { apiRequest } from "./client";
import {
  acceptGenerationOutline,
  askGenerationAssistant,
  closeReviewRound,
  discardGenerationRun,
  getGenerationReview,
  getGenerationRun,
  isInFlightGenerationPhase,
  isTerminalGenerationPhase,
  listTopicGenerationRuns,
  fetchTopicItemStats,
  publishGenerationRun,
  rejectGenerationItems,
  deleteUnpublishedTopic,
  retryGenerationRun,
  submitReviewDecision,
  submitSubjectGenerationRun,
  submitTopicGenerationRun,
} from "./generation";
import type { GenerationRun } from "./types";

function runFixture(over: Partial<GenerationRun> = {}): GenerationRun {
  return {
    id: "run-1",
    topic_id: "topic-1",
    title: "Unit source",
    phase: "outline_review",
    target_item_count: 80,
    submitted_by_user_id: "u1",
    intake_version_id: "v1",
    failure_reason: null,
    outline: { target_item_count: 80, nodes: [] },
    draft_lesson_markdown: null,
    jobs: [],
    created_at: "2026-09-09T00:00:00Z",
    ...over,
  };
}

describe("generation helpers", () => {
  beforeEach(() => {
    vi.mocked(apiRequest).mockReset();
  });

  it("keeps indexing and outlining in flight", () => {
    expect(isTerminalGenerationPhase("indexing")).toBe(false);
    expect(isTerminalGenerationPhase("outlining")).toBe(false);
    expect(isTerminalGenerationPhase("outline_review")).toBe(true);
    expect(isTerminalGenerationPhase("failed")).toBe(true);
    expect(isTerminalGenerationPhase("generating")).toBe(true);
    expect(isTerminalGenerationPhase("qa_review")).toBe(true);
    expect(isTerminalGenerationPhase("ready" as never)).toBe(false);
    expect(isInFlightGenerationPhase("indexing")).toBe(true);
    expect(isInFlightGenerationPhase("qa_review")).toBe(true);
    expect(isInFlightGenerationPhase("published")).toBe(false);
    expect(isInFlightGenerationPhase("failed")).toBe(false);
  });

  it("posts topic generation as FormData on the admin path", async () => {
    vi.mocked(apiRequest).mockResolvedValue({
      run_id: "run-1",
      topic_id: "topic-1",
      phase: "indexing",
    });

    const file = new File(["%PDF"], "unit.pdf", { type: "application/pdf" });
    await submitTopicGenerationRun("topic-1", file, "Unit source", 80);

    expect(apiRequest).toHaveBeenCalledTimes(1);
    const [path, options] = vi.mocked(apiRequest).mock.calls[0]!;
    expect(path).toBe("/admin/topics/topic-1/generation-runs");
    expect(options?.method).toBe("POST");
    expect(options?.body).toBeInstanceOf(FormData);
    const body = options!.body as FormData;
    expect(body.get("title")).toBe("Unit source");
    expect(body.get("target_item_count")).toBe("80");
    expect(body.get("file")).toBeInstanceOf(File);
  });

  it("posts subject generation as FormData on the admin path", async () => {
    vi.mocked(apiRequest).mockResolvedValue({
      run_id: "run-2",
      topic_id: "topic-2",
      phase: "indexing",
    });

    const file = new File(["%PDF"], "topic.pdf", { type: "application/pdf" });
    await submitSubjectGenerationRun("subj-1", file, "Fractions", 70, "Grade 8");

    expect(apiRequest).toHaveBeenCalledTimes(1);
    const [path, options] = vi.mocked(apiRequest).mock.calls[0]!;
    expect(path).toBe("/admin/subjects/subj-1/generation-runs");
    expect(options?.method).toBe("POST");
    expect(options?.body).toBeInstanceOf(FormData);
    const body = options!.body as FormData;
    expect(body.get("title")).toBe("Fractions");
    expect(body.get("target_item_count")).toBe("70");
    expect(body.get("grade_name")).toBe("Grade 8");
    expect(body.get("file")).toBeInstanceOf(File);
  });

  it("loads a run by id", async () => {
    vi.mocked(apiRequest).mockResolvedValue({
      id: "run-1",
      topic_id: "topic-1",
      title: "Unit source",
      phase: "failed",
      target_item_count: 80,
      submitted_by_user_id: "u1",
      intake_version_id: "v1",
      failure_reason: "LLM down",
      outline: null,
      draft_lesson_markdown: null,
      jobs: [],
      created_at: "2026-09-09T00:00:00Z",
    });
    const run = await getGenerationRun("run-1");
    expect(run.failure_reason).toBe("LLM down");
  });

  it("lists generation runs for a topic", async () => {
    vi.mocked(apiRequest).mockResolvedValue([runFixture({ id: "run-2" })]);

    const runs = await listTopicGenerationRuns("topic-1");

    expect(runs[0]?.id).toBe("run-2");
    expect(apiRequest).toHaveBeenCalledWith("/teaching/topics/topic-1/generation-runs");
  });

  it("loads review workspace and posts teacher decisions and admin close", async () => {
    vi.mocked(apiRequest)
      .mockResolvedValueOnce({
        run_id: "run-1",
        open_round: { id: "round-1" },
        active_revision: { id: "rev-1" },
      })
      .mockResolvedValueOnce({ id: "dec-1", verdict: "approve" })
      .mockResolvedValueOnce({ kind: "outline_accepted", run_id: "run-1" });

    await getGenerationReview("run-1");
    await submitReviewDecision("run-1", "round-1", {
      revision_id: "rev-1",
      verdict: "approve",
    });
    await closeReviewRound("run-1", "round-1", {
      revision_id: "rev-1",
      action: "accept_current",
    });

    expect(apiRequest).toHaveBeenNthCalledWith(1, "/teaching/generation-runs/run-1/review");
    expect(apiRequest).toHaveBeenNthCalledWith(
      2,
      "/teaching/generation-runs/run-1/review-rounds/round-1/decisions",
      { method: "POST", body: { revision_id: "rev-1", verdict: "approve" } },
    );
    expect(apiRequest).toHaveBeenNthCalledWith(
      3,
      "/teaching/generation-runs/run-1/review-rounds/round-1/close",
      { method: "POST", body: { revision_id: "rev-1", action: "accept_current" } },
    );
  });

  it("posts a generation assistant turn without submitting a decision", async () => {
    vi.mocked(apiRequest).mockResolvedValue({
      content: "The outcome is too broad.",
      citations: [],
      draft_change_request: {
        target: { kind: "outline_node", node_key: "node-1" },
        field: "proposed_outcomes",
        kind: "curriculum_alignment",
        comment: "Add an outcome for variables on both sides.",
      },
    });

    const reply = await askGenerationAssistant("run-1", {
      revision_id: "rev-1",
      target: { kind: "outline_node", node_key: "node-1" },
      message: "Draft a tighter outcome.",
    });

    expect(reply.draft_change_request?.comment).toContain("variables");
    expect(apiRequest).toHaveBeenCalledWith("/teaching/generation-runs/run-1/assistant/turns", {
      method: "POST",
      body: {
        revision_id: "rev-1",
        target: { kind: "outline_node", node_key: "node-1" },
        message: "Draft a tighter outcome.",
      },
    });
  });

  it("posts accept, discard, and retry on the teaching run paths", async () => {
    vi.mocked(apiRequest)
      .mockResolvedValueOnce(runFixture({ phase: "generating" }))
      .mockResolvedValueOnce(runFixture({ phase: "discarded" }))
      .mockResolvedValueOnce(runFixture({ phase: "outlining" }));

    const accepted = await acceptGenerationOutline("run-1");
    const discarded = await discardGenerationRun("run-1");
    const retried = await retryGenerationRun("run-1");

    expect(accepted.phase).toBe("generating");
    expect(discarded.phase).toBe("discarded");
    expect(retried.phase).toBe("outlining");
    expect(apiRequest).toHaveBeenNthCalledWith(
      1,
      "/teaching/generation-runs/run-1/accept-outline",
      { method: "POST" },
    );
    expect(apiRequest).toHaveBeenNthCalledWith(2, "/teaching/generation-runs/run-1/discard", {
      method: "POST",
    });
    expect(apiRequest).toHaveBeenNthCalledWith(3, "/teaching/generation-runs/run-1/retry", {
      method: "POST",
    });
  });

  it("deletes an unpublished topic", async () => {
    vi.mocked(apiRequest).mockResolvedValueOnce(undefined);
    await deleteUnpublishedTopic("topic-2");
    expect(apiRequest).toHaveBeenCalledWith("/admin/topics/topic-2", { method: "DELETE" });
  });

  it("posts publish and reject-items on the teaching run paths", async () => {
    vi.mocked(apiRequest)
      .mockResolvedValueOnce({
        run_id: "run-1",
        topic_id: "topic-1",
        lesson_version_id: "lesson-1",
        quiz_version_id: "quiz-1",
        item_count: 60,
      })
      .mockResolvedValueOnce(runFixture({ phase: "generating" }));

    const published = await publishGenerationRun("run-1");
    const rejected = await rejectGenerationItems("run-1", ["q1"]);

    expect(published.item_count).toBe(60);
    expect(rejected.phase).toBe("generating");
    expect(apiRequest).toHaveBeenNthCalledWith(1, "/teaching/generation-runs/run-1/publish", {
      method: "POST",
    });
    expect(apiRequest).toHaveBeenNthCalledWith(2, "/teaching/generation-runs/run-1/reject-items", {
      method: "POST",
      body: { question_ids: ["q1"] },
    });
  });

  it("loads topic item stats from the admin topic path", async () => {
    const payload = {
      topic_id: "topic-1",
      quiz_version_id: "quiz-1",
      minimum_n: 20,
      items: [],
    };
    vi.mocked(apiRequest).mockResolvedValue(payload);

    await expect(fetchTopicItemStats("topic 1")).resolves.toEqual(payload);
    expect(apiRequest).toHaveBeenCalledWith("/admin/topics/topic%201/item-stats");
  });
});
