import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./types";

vi.mock("./client", () => ({
  apiStream: vi.fn(),
}));

import { apiStream } from "./client";
import {
  PROGRESS_RECONNECT_FACTOR,
  PROGRESS_RECONNECT_INITIAL_MS,
  PROGRESS_RECONNECT_MAX_MS,
  progressReconnectDelayMs,
  runSubject,
  subscribeProgress,
  versionSubject,
  watchProgress,
} from "./progress";
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

function streamThenError(chunks: string[], error: Error = new TypeError("connection reset")): Response {
  const encoder = new TextEncoder();
  let index = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (index >= chunks.length) {
        controller.error(error);
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

function heldStream(signal: AbortSignal | undefined): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const fail = () => {
        controller.error(new DOMException("Aborted", "AbortError"));
      };
      if (signal?.aborted) {
        fail();
        return;
      }
      signal?.addEventListener("abort", fail, { once: true });
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function snapshotFrame(phase: string, id = `run-1:${phase}`): string {
  return `id: ${id}\nevent: snapshot\ndata: ${JSON.stringify({ id: "run-1", phase })}\n\n`;
}

type Settled<T> = { ok: true; value: T } | { ok: false; error: unknown };

function settle<T>(promise: Promise<T>): Promise<Settled<T>> {
  return promise.then(
    (value) => ({ ok: true, value }),
    (error: unknown) => ({ ok: false, error }),
  );
}

describe("progressReconnectDelayMs", () => {
  it("grows exponentially until the cap and stays inside the jitter window", () => {
    let previousHigh = 0;
    for (let attempt = 0; attempt < 8; attempt += 1) {
      const low = progressReconnectDelayMs(attempt, 0);
      const high = progressReconnectDelayMs(attempt, 1);
      const exponential = Math.min(
        PROGRESS_RECONNECT_MAX_MS,
        PROGRESS_RECONNECT_INITIAL_MS * PROGRESS_RECONNECT_FACTOR ** attempt,
      );
      expect(low).toBe(Math.floor(exponential * 0.5));
      expect(high).toBe(exponential);
      expect(low).toBeGreaterThan(0);
      expect(low).toBeLessThanOrEqual(high);
      expect(high).toBeLessThanOrEqual(PROGRESS_RECONNECT_MAX_MS);
      const mid = progressReconnectDelayMs(attempt, 0.25);
      expect(mid).toBeGreaterThanOrEqual(low);
      expect(mid).toBeLessThanOrEqual(high);
      if (attempt > 0 && previousHigh < PROGRESS_RECONNECT_MAX_MS) {
        expect(high).toBeGreaterThan(previousHigh);
      }
      previousHigh = high;
    }
    expect(progressReconnectDelayMs(40, 0)).toBe(Math.floor(PROGRESS_RECONNECT_MAX_MS * 0.5));
    expect(progressReconnectDelayMs(40, 1)).toBe(PROGRESS_RECONNECT_MAX_MS);
    expect(progressReconnectDelayMs(1, -5)).toBe(progressReconnectDelayMs(1, 0));
    expect(progressReconnectDelayMs(1, 5)).toBe(progressReconnectDelayMs(1, 1));
  });
});

describe("progress SSE client", () => {
  beforeEach(() => {
    vi.mocked(apiStream).mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

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

  it("raises stream error events", async () => {
    vi.mocked(apiStream).mockResolvedValue(
      streamResponse(['event: error\ndata: {"detail":"gone","status":404}\n\n']),
    );
    await expect(subscribeProgress(runSubject("run-1"), { onSnapshot: vi.fn() })).rejects.toBeInstanceOf(
      ApiError,
    );
    expect(apiStream).toHaveBeenCalledTimes(1);
  });

  it("reconnects after a mid-stream error and resumes with Last-Event-ID", async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);
    const onSnapshot = vi.fn();
    vi.mocked(apiStream)
      .mockResolvedValueOnce(streamThenError([snapshotFrame("indexing", "run-1:abc")]))
      .mockResolvedValueOnce(
        streamResponse([
          snapshotFrame("outlining", "run-1:def"),
          'event: close\ndata: {"reason":"terminal"}\n\n',
        ]),
      );

    const pending = settle(subscribeProgress(runSubject("run-1"), { onSnapshot }));
    const delay = progressReconnectDelayMs(0, 0);

    await vi.advanceTimersByTimeAsync(0);
    expect(apiStream).toHaveBeenCalledTimes(1);
    expect(apiStream).toHaveBeenNthCalledWith(1, "/progress/runs/run-1", {
      signal: undefined,
      lastEventId: undefined,
    });

    await vi.advanceTimersByTimeAsync(delay - 1);
    expect(apiStream).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1);
    const result = await pending;
    expect(result.ok).toBe(true);
    expect(apiStream).toHaveBeenNthCalledWith(2, "/progress/runs/run-1", {
      signal: undefined,
      lastEventId: "run-1:abc",
    });
    expect(onSnapshot.mock.calls.map((call) => call[0]?.phase)).toEqual(["indexing", "outlining"]);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("reconnects when the stream ends without a close event", async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);
    const onSnapshot = vi.fn();
    vi.mocked(apiStream)
      .mockResolvedValueOnce(streamResponse([snapshotFrame("indexing", "run-1:abc")]))
      .mockResolvedValueOnce(
        streamResponse([
          snapshotFrame("generating", "run-1:def"),
          'event: close\ndata: {"reason":"terminal"}\n\n',
        ]),
      );

    const pending = settle(
      watchProgress(runSubject("run-1"), {
        onSnapshot,
      }),
    );
    await vi.advanceTimersByTimeAsync(progressReconnectDelayMs(0, 0));
    const result = await pending;
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value).toMatchObject({ phase: "generating" });
    }
    expect(onSnapshot.mock.calls.map((call) => call[0]?.phase)).toEqual(["indexing", "generating"]);
    expect(apiStream).toHaveBeenNthCalledWith(2, "/progress/runs/run-1", {
      signal: undefined,
      lastEventId: "run-1:abc",
    });
  });

  it("waits out growing delays and then stays at the cap", async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);
    vi.mocked(apiStream).mockRejectedValue(new TypeError("connection reset"));
    const abort = new AbortController();
    const pending = settle(
      subscribeProgress(runSubject("run-1"), { signal: abort.signal, onSnapshot: vi.fn() }),
    );

    await vi.advanceTimersByTimeAsync(0);
    expect(apiStream).toHaveBeenCalledTimes(1);

    const observed: number[] = [];
    for (let attempt = 0; attempt < 8; attempt += 1) {
      const delay = progressReconnectDelayMs(attempt, 0);
      observed.push(delay);
      await vi.advanceTimersByTimeAsync(delay - 1);
      expect(apiStream).toHaveBeenCalledTimes(attempt + 1);
      await vi.advanceTimersByTimeAsync(1);
      expect(apiStream).toHaveBeenCalledTimes(attempt + 2);
    }

    expect(observed[0]).toBe(Math.floor(PROGRESS_RECONNECT_INITIAL_MS * 0.5));
    expect(observed[1]).toBe(observed[0] * 2);
    expect(observed[2]).toBe(observed[1] * 2);
    const capped = Math.floor(PROGRESS_RECONNECT_MAX_MS * 0.5);
    expect(observed.at(-1)).toBe(capped);
    expect(observed.at(-2)).toBe(capped);

    abort.abort();
    const result = await pending;
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toMatchObject({ name: "AbortError" });
    }
    const callsAtAbort = vi.mocked(apiStream).mock.calls.length;
    await vi.advanceTimersByTimeAsync(PROGRESS_RECONNECT_MAX_MS * 2);
    expect(apiStream).toHaveBeenCalledTimes(callsAtAbort);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("uses the upper jitter bound when scheduling the reconnect", async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(1);
    vi.mocked(apiStream).mockRejectedValue(new TypeError("connection reset"));
    const abort = new AbortController();
    const pending = settle(
      subscribeProgress(runSubject("run-1"), { signal: abort.signal, onSnapshot: vi.fn() }),
    );

    await vi.advanceTimersByTimeAsync(0);
    expect(apiStream).toHaveBeenCalledTimes(1);

    const half = progressReconnectDelayMs(0, 0);
    const full = progressReconnectDelayMs(0, 1);
    expect(full).toBeGreaterThan(half);
    await vi.advanceTimersByTimeAsync(half);
    expect(apiStream).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(full - half);
    expect(apiStream).toHaveBeenCalledTimes(2);

    abort.abort();
    await pending;
    expect(vi.getTimerCount()).toBe(0);
  });

  it("resets backoff after a snapshot and after a heartbeat", async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);
    const base = progressReconnectDelayMs(0, 0);
    const grown = progressReconnectDelayMs(1, 0);
    expect(grown).toBeGreaterThan(base);

    vi.mocked(apiStream)
      .mockRejectedValueOnce(new TypeError("connection reset"))
      .mockResolvedValueOnce(streamThenError([snapshotFrame("indexing", "run-1:abc")]))
      .mockRejectedValueOnce(new TypeError("connection reset"))
      .mockResolvedValueOnce(streamResponse([": heartbeat\n\n"]))
      .mockRejectedValue(new TypeError("connection reset"));

    const abort = new AbortController();
    const pending = settle(
      subscribeProgress(runSubject("run-1"), { signal: abort.signal, onSnapshot: vi.fn() }),
    );

    await vi.advanceTimersByTimeAsync(0);
    expect(apiStream).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(base - 1);
    expect(apiStream).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(apiStream).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(base - 1);
    expect(apiStream).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(apiStream).toHaveBeenCalledTimes(3);

    await vi.advanceTimersByTimeAsync(grown - 1);
    expect(apiStream).toHaveBeenCalledTimes(3);
    await vi.advanceTimersByTimeAsync(1);
    expect(apiStream).toHaveBeenCalledTimes(4);

    await vi.advanceTimersByTimeAsync(base - 1);
    expect(apiStream).toHaveBeenCalledTimes(4);
    await vi.advanceTimersByTimeAsync(1);
    expect(apiStream).toHaveBeenCalledTimes(5);

    abort.abort();
    await pending;
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each(["published", "failed", "discarded"] as const)(
    "stops reconnecting when a run reaches %s",
    async (phase) => {
      vi.useFakeTimers();
      vi.spyOn(Math, "random").mockReturnValue(0);
      const onSnapshot = vi.fn();
      vi.mocked(apiStream).mockResolvedValue(streamThenError([snapshotFrame(phase)]));

      const pending = settle(subscribeProgress(runSubject("run-1"), { onSnapshot }));
      await vi.advanceTimersByTimeAsync(0);
      const result = await pending;
      expect(result.ok).toBe(true);
      expect(onSnapshot.mock.calls.map((call) => call[0]?.phase)).toEqual([phase]);
      await vi.advanceTimersByTimeAsync(PROGRESS_RECONNECT_MAX_MS);
      expect(apiStream).toHaveBeenCalledTimes(1);
      expect(vi.getTimerCount()).toBe(0);
    },
  );

  it.each(["ready", "failed", "published", "superseded", "archived"] as const)(
    "stops reconnecting when a version reaches %s",
    async (lifecycle) => {
      vi.useFakeTimers();
      vi.spyOn(Math, "random").mockReturnValue(0);
      const onSnapshot = vi.fn();
      vi.mocked(apiStream).mockResolvedValue(
        streamThenError([
          `id: version:ver-1:${lifecycle}\nevent: snapshot\ndata: ${JSON.stringify({
            id: "ver-1",
            document_id: "doc-1",
            lifecycle_status: lifecycle,
          })}\n\n`,
        ]),
      );

      const pending = settle(subscribeProgress(versionSubject("ver-1"), { onSnapshot }));
      await vi.advanceTimersByTimeAsync(0);
      const result = await pending;
      expect(result.ok).toBe(true);
      expect(onSnapshot).toHaveBeenCalledWith(expect.objectContaining({ lifecycle_status: lifecycle }));
      await vi.advanceTimersByTimeAsync(PROGRESS_RECONNECT_MAX_MS);
      expect(apiStream).toHaveBeenCalledTimes(1);
      expect(apiStream).toHaveBeenCalledWith("/progress/versions/ver-1", {
        signal: undefined,
        lastEventId: undefined,
      });
    },
  );

  it("keeps following a parked review phase after the stream drops", async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);
    const onSnapshot = vi.fn();
    vi.mocked(apiStream)
      .mockResolvedValueOnce(streamResponse([snapshotFrame("outline_review", "run-1:hitl")]))
      .mockResolvedValueOnce(
        streamResponse([
          snapshotFrame("generating", "run-1:next"),
          'event: close\ndata: {"reason":"terminal"}\n\n',
        ]),
      );

    const pending = settle(subscribeProgress(runSubject("run-1"), { onSnapshot }));
    await vi.advanceTimersByTimeAsync(progressReconnectDelayMs(0, 0));
    const result = await pending;
    expect(result.ok).toBe(true);
    expect(onSnapshot.mock.calls.map((call) => call[0]?.phase)).toEqual([
      "outline_review",
      "generating",
    ]);
    expect(apiStream).toHaveBeenNthCalledWith(2, "/progress/runs/run-1", {
      signal: undefined,
      lastEventId: "run-1:hitl",
    });
  });

  it("does not retry auth, not-found, or a broken snapshot", async () => {
    vi.useFakeTimers();
    const cases: Array<{ error: Error; status?: number }> = [
      { error: new ApiError("unauthorized", 401, null), status: 401 },
      { error: new ApiError("forbidden", 403, null), status: 403 },
      { error: new ApiError("missing", 404, null), status: 404 },
    ];
    for (const item of cases) {
      vi.mocked(apiStream).mockReset();
      vi.mocked(apiStream).mockRejectedValue(item.error);
      const pending = settle(subscribeProgress(runSubject("run-1"), { onSnapshot: vi.fn() }));
      await vi.advanceTimersByTimeAsync(0);
      const result = await pending;
      expect(result.ok).toBe(false);
      if (!result.ok) {
        expect(result.error).toMatchObject({ status: item.status });
      }
      await vi.advanceTimersByTimeAsync(PROGRESS_RECONNECT_MAX_MS);
      expect(apiStream).toHaveBeenCalledTimes(1);
    }

    vi.mocked(apiStream).mockReset();
    vi.mocked(apiStream).mockResolvedValue(streamResponse(["event: snapshot\ndata: not-json\n\n"]));
    const broken = settle(subscribeProgress(runSubject("run-1"), { onSnapshot: vi.fn() }));
    await vi.advanceTimersByTimeAsync(0);
    const brokenResult = await broken;
    expect(brokenResult.ok).toBe(false);
    if (!brokenResult.ok) {
      expect(brokenResult.error).toBeInstanceOf(SyntaxError);
    }
    await vi.advanceTimersByTimeAsync(PROGRESS_RECONNECT_MAX_MS);
    expect(apiStream).toHaveBeenCalledTimes(1);
  });

  it("retries a gateway error and then applies the resumed snapshot", async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);
    const onSnapshot = vi.fn();
    vi.mocked(apiStream)
      .mockRejectedValueOnce(new ApiError("bad gateway", 502, null))
      .mockResolvedValueOnce(
        streamResponse([
          `event: snapshot\ndata: ${JSON.stringify(runFixture({ phase: "indexing" }))}\n\n`,
          'event: close\ndata: {"reason":"terminal"}\n\n',
        ]),
      );

    const pending = settle(watchProgress(runSubject("run-1"), { onSnapshot }));
    await vi.advanceTimersByTimeAsync(0);
    expect(apiStream).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(progressReconnectDelayMs(0, 0));
    const result = await pending;
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.value).toMatchObject({ phase: "indexing" });
    }
    expect(apiStream).toHaveBeenCalledTimes(2);
    expect(onSnapshot).toHaveBeenCalledTimes(1);
  });

  it("sends a caller Last-Event-ID on the first request", async () => {
    vi.mocked(apiStream).mockResolvedValue(
      streamResponse(['event: close\ndata: {"reason":"terminal"}\n\n']),
    );
    await subscribeProgress(runSubject("run-1"), {
      lastEventId: "run-1:prior",
      onSnapshot: vi.fn(),
    });
    expect(apiStream).toHaveBeenCalledWith("/progress/runs/run-1", {
      signal: undefined,
      lastEventId: "run-1:prior",
    });
  });

  it("does not retry when the snapshot handler throws", async () => {
    vi.useFakeTimers();
    vi.mocked(apiStream).mockResolvedValue(streamResponse([snapshotFrame("indexing")]));
    const pending = settle(
      subscribeProgress(runSubject("run-1"), {
        onSnapshot: () => {
          throw new Error("ui blew up");
        },
      }),
    );
    await vi.advanceTimersByTimeAsync(0);
    const result = await pending;
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toMatchObject({ message: "ui blew up" });
    }
    await vi.advanceTimersByTimeAsync(PROGRESS_RECONNECT_MAX_MS);
    expect(apiStream).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("clears the reconnect timer when the caller aborts during backoff", async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);
    vi.mocked(apiStream).mockRejectedValue(new TypeError("connection reset"));
    const abort = new AbortController();
    const pending = settle(
      subscribeProgress(runSubject("run-1"), { signal: abort.signal, onSnapshot: vi.fn() }),
    );

    await vi.advanceTimersByTimeAsync(0);
    expect(apiStream).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(1);

    abort.abort();
    const result = await pending;
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toMatchObject({ name: "AbortError" });
    }
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(PROGRESS_RECONNECT_MAX_MS * 2);
    expect(apiStream).toHaveBeenCalledTimes(1);
  });

  it("stops an open stream when the caller unsubscribes", async () => {
    vi.useFakeTimers();
    const abort = new AbortController();
    vi.mocked(apiStream).mockImplementation(async (_path, options) => heldStream(options?.signal));
    const pending = settle(
      subscribeProgress(runSubject("run-1"), { signal: abort.signal, onSnapshot: vi.fn() }),
    );

    await vi.advanceTimersByTimeAsync(0);
    expect(apiStream).toHaveBeenCalledTimes(1);

    abort.abort();
    const result = await pending;
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toMatchObject({ name: "AbortError" });
    }
    await vi.advanceTimersByTimeAsync(PROGRESS_RECONNECT_MAX_MS * 2);
    expect(apiStream).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not open a stream when the signal is already aborted", async () => {
    const abort = new AbortController();
    abort.abort();
    await expect(
      subscribeProgress(runSubject("run-1"), { signal: abort.signal, onSnapshot: vi.fn() }),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(apiStream).not.toHaveBeenCalled();
  });
});
