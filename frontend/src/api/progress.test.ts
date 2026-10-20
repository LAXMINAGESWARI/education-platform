import { describe, expect, it, vi } from "vitest";

import { ApiError } from "./types";

vi.mock("./client", () => ({
  apiStream: vi.fn(),
}));

import { apiStream } from "./client";
import { parseProgressEvent, runSubject, subscribeProgress, watchProgress } from "./progress";
import type { GenerationRun } from "./types";

function runFixture(over: Partial<GenerationRun> = {}): GenerationRun {
  return {
    id: "run-1",
    topic_id: "topic-1",
    title: "Unit source",
    phase: "indexing",
    target_item_count: 80,
    submitted_by_user_id: "u1",
    intake_version_id: "v1",
    failure_reason: null,
    outline: null,
    draft_lesson_markdown: null,
    jobs: [],
    created_at: "2026-09-13T00:00:00Z",
    ...over,
  };
}

function streamResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  let index = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (index >= chunks.length) {
        controller.close();
        return;
      }
      controller.enqueue(encoder.encode(chunks[index]));
      index += 1;
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

describe("progress SSE client", () => {
  it("emits snapshots and stops on close", async () => {
    const onSnapshot = vi.fn();
    vi.mocked(apiStream).mockResolvedValue(
      streamResponse([
        'id: run-1:abc\nevent: snapshot\ndata: {"id":"run-1","phase":"indexing"}\n\n',
        'event: snapshot\ndata: {"id":"run-1","phase":"failed"}\n\n',
        'event: close\ndata: {"reason":"terminal"}\n\n',
      ]),
    );

    await subscribeProgress(runSubject("run-1"), { onSnapshot });

    expect(apiStream).toHaveBeenCalledWith("/progress/runs/run-1", {
      signal: undefined,
      lastEventId: undefined,
    });
    expect(onSnapshot.mock.calls.map((call) => call[0]?.phase)).toEqual(["indexing", "failed"]);
  });

  it("returns the last snapshot from watchProgress", async () => {
    vi.mocked(apiStream).mockResolvedValue(
      streamResponse([
        `event: snapshot\ndata: ${JSON.stringify(runFixture({ phase: "failed" }))}\n\n`,
        'event: close\ndata: {"reason":"terminal"}\n\n',
      ]),
    );
    const snapshot = await watchProgress(runSubject("run-1"));
    expect((snapshot as GenerationRun).phase).toBe("failed");
  });

  it("parses the shared progress event union", () => {
    const upload = parseProgressEvent("progress", {
      v: 1,
      kind: "upload",
      subject_id: "ver-1",
      seq: 4,
      stage: "embedding",
      status: "running",
      percent: 70,
      current: 5,
      total: 10,
      unit: "chunk",
      message: "Embedding 5 of 10 chunks.",
      updated_at: "2026-10-20T10:15:03+00:00",
    });
    expect(upload).toEqual({
      event: "progress",
      data: expect.objectContaining({ kind: "upload", stage: "embedding", seq: 4, percent: 70 }),
    });

    const delta = parseProgressEvent("answer_delta", { text: "Four " });
    expect(delta).toEqual({ event: "answer_delta", data: { text: "Four " } });

    const finalEvent = parseProgressEvent("final", {
      natural_answer: "Four students",
      confidence: "high",
      provenance: "roster",
    });
    expect(finalEvent?.event).toBe("final");

    expect(parseProgressEvent("progress", { v: 2, kind: "upload" })).toBeNull();
    expect(parseProgressEvent("progress", { v: 1, kind: "upload", stage: "nope" })).toBeNull();
    expect(parseProgressEvent("refusal", { message: "I can't answer that." })?.event).toBe("refusal");
  });

  it("raises stream error events", async () => {
    vi.mocked(apiStream).mockResolvedValue(
      streamResponse(['event: error\ndata: {"detail":"gone","status":404}\n\n']),
    );
    await expect(subscribeProgress(runSubject("run-1"), { onSnapshot: vi.fn() })).rejects.toBeInstanceOf(
      ApiError,
    );
  });
});
