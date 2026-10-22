import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./types";

vi.mock("./client", () => ({
  apiStream: vi.fn(),
}));

import { apiStream } from "./client";
import { TEXT_TO_SQL_STAGE_ORDER, stageVisuals } from "./stageOrder";
import { progressReconnectDelayMs } from "./sseReconnect";
import {
  TEXT_TO_SQL_STAGES,
  askQuestionStream,
  parseTextToSqlFrame,
  type TextToSqlProgress,
} from "./textToSql";

const REQUEST_ID = "2f1c0c2e-7b1a-4e0a-9c3d-1a2b3c4d5e6f";

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

function progressFrame(
  stage: string,
  status: string,
  seq: number,
  message: string | null,
): string {
  return `event: progress\ndata: ${JSON.stringify({
    v: 1,
    kind: "text_to_sql",
    subject_id: REQUEST_ID,
    seq,
    stage,
    status,
    percent: null,
    current: null,
    total: null,
    unit: null,
    message,
    updated_at: "2026-10-20T11:00:00Z",
  })}\n\n`;
}

function finalFrame(answer = "You teach 10."): string {
  return `event: final\ndata: ${JSON.stringify({
    natural_answer: answer,
    confidence: "high",
    provenance: "Queried: student_profiles.",
  })}\n\n`;
}

type Settled<T> = { ok: true; value: T } | { ok: false; error: unknown };

function settle<T>(promise: Promise<T>): Promise<Settled<T>> {
  return promise.then(
    (value) => ({ ok: true, value }),
    (error: unknown) => ({ ok: false, error }),
  );
}

describe("parseTextToSqlFrame", () => {
  it("lists the stages in pipeline order", () => {
    expect(TEXT_TO_SQL_STAGES).toEqual([
      "checking",
      "understanding",
      "writing_query",
      "validating",
      "running",
      "summarising",
    ]);
  });

  it("parses each frame type", () => {
    expect(parseTextToSqlFrame({ event: "heartbeat", data: "" })).toEqual({ type: "heartbeat" });

    const progress = parseTextToSqlFrame({
      event: "progress",
      data: JSON.stringify({
        v: 1,
        kind: "text_to_sql",
        subject_id: REQUEST_ID,
        seq: 1,
        stage: "checking",
        status: "running",
        percent: null,
        current: null,
        total: null,
        unit: null,
        message: "Checking your question",
        updated_at: "2026-10-20T11:00:00Z",
      }),
    });
    expect(progress).toMatchObject({
      type: "progress",
      progress: { stage: "checking", status: "running", message: "Checking your question", percent: null },
    });

    expect(
      parseTextToSqlFrame({ event: "answer_delta", data: JSON.stringify({ text: "Aisha " }) }),
    ).toEqual({ type: "answer_delta", text: "Aisha " });

    expect(
      parseTextToSqlFrame({
        event: "table",
        data: JSON.stringify({
          columns: ["Full name", "Mastery percent"],
          rows: [["Aisha Rahman", 82]],
        }),
      }),
    ).toEqual({
      type: "table",
      table: { columns: ["Full name", "Mastery percent"], rows: [["Aisha Rahman", 82]] },
    });

    expect(
      parseTextToSqlFrame({
        event: "final",
        data: JSON.stringify({ natural_answer: "Ten.", confidence: null, provenance: null }),
      }),
    ).toEqual({
      type: "final",
      answer: { natural_answer: "Ten.", confidence: null, provenance: null },
    });

    expect(
      parseTextToSqlFrame({ event: "refusal", data: JSON.stringify({ message: "Out of scope." }) }),
    ).toEqual({ type: "refusal", message: "Out of scope." });

    expect(
      parseTextToSqlFrame({
        event: "error",
        data: JSON.stringify({ message: "That took too long to answer — please try again." }),
      }),
    ).toEqual({
      type: "error",
      message: "That took too long to answer — please try again.",
    });

    expect(parseTextToSqlFrame({ event: "policy", data: "{}" })).toEqual({ type: "ignore" });
  });

  it("rejects a progress frame that is not text-to-SQL", () => {
    expect(() =>
      parseTextToSqlFrame({
        event: "progress",
        data: JSON.stringify({
          v: 1,
          kind: "upload",
          subject_id: REQUEST_ID,
          seq: 1,
          stage: "checking",
          status: "running",
          percent: null,
          current: null,
          total: null,
          unit: null,
          message: null,
          updated_at: "2026-10-20T11:00:00Z",
        }),
      }),
    ).toThrow(SyntaxError);
  });
});

describe("askQuestionStream", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("posts a client request id and stops on final, refusal, and error", async () => {
    vi.spyOn(crypto, "randomUUID").mockReturnValue(REQUEST_ID);
    vi.mocked(apiStream).mockResolvedValueOnce(
      streamResponse([
        ...TEXT_TO_SQL_STAGES.map((stage, index) =>
          progressFrame(stage, index === TEXT_TO_SQL_STAGES.length - 1 ? "running" : "done", index + 1, null),
        ),
        finalFrame(),
      ]),
    );

    const stages: string[] = [];
    const result = await askQuestionStream("How many students do I teach?", {
      onProgress: (progress) => {
        stages.push(progress.stage);
      },
    });

    expect(result).toMatchObject({
      kind: "final",
      requestId: REQUEST_ID,
      answer: { natural_answer: "You teach 10.", confidence: "high" },
    });
    expect(stages).toEqual([...TEXT_TO_SQL_STAGES]);
    expect(apiStream).toHaveBeenCalledTimes(1);
    expect(apiStream).toHaveBeenCalledWith("/text-to-sql/ask/stream", {
      method: "POST",
      body: { question: "How many students do I teach?", request_id: REQUEST_ID },
      signal: expect.any(AbortSignal),
    });

    vi.mocked(apiStream).mockResolvedValueOnce(
      streamResponse(['event: refusal\ndata: {"message":"Out of scope."}\n\n']),
    );
    await expect(askQuestionStream("weather", { requestId: REQUEST_ID })).resolves.toEqual({
      kind: "refusal",
      requestId: REQUEST_ID,
      message: "Out of scope.",
    });

    vi.mocked(apiStream).mockResolvedValueOnce(
      streamResponse(['event: error\ndata: {"message":"That took too long to answer — please try again."}\n\n']),
    );
    await expect(askQuestionStream("slow", { requestId: REQUEST_ID })).resolves.toEqual({
      kind: "error",
      requestId: REQUEST_ID,
      message: "That took too long to answer — please try again.",
    });
    expect(apiStream).toHaveBeenCalledTimes(3);
  });

  it("re-posts the same request id and replays the stored final without a second answer", async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);
    vi.spyOn(crypto, "randomUUID").mockReturnValue(REQUEST_ID);
    const log: string[] = [];
    vi.mocked(apiStream)
      .mockResolvedValueOnce(
        streamResponse([
          progressFrame("summarising", "running", 1, "Writing the answer"),
          'event: answer_delta\ndata: {"text":"Hello"}\n\n',
        ]),
      )
      .mockResolvedValueOnce(
        streamResponse([
          progressFrame("summarising", "done", 2, "Answer ready"),
          finalFrame("You teach 10."),
        ]),
      );

    const pending = askQuestionStream("How many students do I teach?", {
      onProgress: (progress) => {
        log.push(`progress:${progress.seq}:${progress.stage}`);
      },
      onAnswerDelta: (text) => {
        log.push(`delta:${text}`);
      },
      onAnswerReset: () => {
        log.push("reset");
      },
    });

    await vi.advanceTimersByTimeAsync(0);
    expect(log).toEqual(["progress:1:summarising", "delta:Hello", "reset"]);
    await vi.advanceTimersByTimeAsync(progressReconnectDelayMs(0, 0));
    const result = await pending;

    expect(result).toMatchObject({ kind: "final", requestId: REQUEST_ID });
    expect(log).toEqual([
      "progress:1:summarising",
      "delta:Hello",
      "reset",
      "progress:2:summarising",
    ]);
    expect(apiStream).toHaveBeenCalledTimes(2);
    const bodies = vi.mocked(apiStream).mock.calls.map((call) => call[1]?.body);
    expect(bodies).toEqual([
      { question: "How many students do I teach?", request_id: REQUEST_ID },
      { question: "How many students do I teach?", request_id: REQUEST_ID },
    ]);
    expect(vi.mocked(apiStream).mock.calls.every((call) => call[1]?.lastEventId === undefined)).toBe(true);
  });

  it("ignores a lower seq and applies a mid-stream snapshot after reconnect", async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);
    const accepted: TextToSqlProgress[] = [];
    vi.mocked(apiStream)
      .mockResolvedValueOnce(
        streamResponse([
          progressFrame("checking", "running", 1, "Checking your question"),
          progressFrame("running", "running", 4, "Looking up the data"),
          progressFrame("understanding", "running", 2, "Stale"),
        ]),
      )
      .mockResolvedValueOnce(
        streamResponse([
          progressFrame("validating", "running", 1, "Checking the query"),
          finalFrame(),
        ]),
      );

    const pending = askQuestionStream("How many students do I teach?", {
      requestId: REQUEST_ID,
      onProgress: (progress) => {
        accepted.push(progress);
      },
    });

    await vi.advanceTimersByTimeAsync(0);
    expect(accepted.map((progress) => `${progress.seq}:${progress.stage}`)).toEqual([
      "1:checking",
      "4:running",
    ]);
    await vi.advanceTimersByTimeAsync(progressReconnectDelayMs(0, 0));
    await pending;

    expect(accepted.map((progress) => `${progress.seq}:${progress.stage}`)).toEqual([
      "1:checking",
      "4:running",
      "1:validating",
    ]);
    const snapshot = accepted.at(-1) ?? null;
    expect(stageVisuals(TEXT_TO_SQL_STAGE_ORDER, snapshot).map((item) => item.visual)).toEqual([
      "complete",
      "complete",
      "complete",
      "current",
      "pending",
      "pending",
    ]);
  });

  it("does not reconnect after a terminal frame", async () => {
    vi.useFakeTimers();
    vi.mocked(apiStream).mockResolvedValue(
      streamResponse(['event: error\ndata: {"message":"Something went wrong on our end — please try again."}\n\n']),
    );
    const result = await askQuestionStream("How many?", { requestId: REQUEST_ID });
    expect(result.kind).toBe("error");
    await vi.advanceTimersByTimeAsync(60_000);
    expect(apiStream).toHaveBeenCalledTimes(1);
  });

  it("aborts an open stream without retrying", async () => {
    vi.useFakeTimers();
    const abort = new AbortController();
    vi.mocked(apiStream).mockImplementation(
      (_path, options) =>
        new Promise((_resolve, reject) => {
          if (options?.signal?.aborted) {
            reject(new DOMException("Aborted", "AbortError"));
            return;
          }
          options?.signal?.addEventListener(
            "abort",
            () => {
              reject(new DOMException("Aborted", "AbortError"));
            },
            { once: true },
          );
        }),
    );
    const pending = settle(askQuestionStream("How many?", { signal: abort.signal, requestId: REQUEST_ID }));
    await vi.advanceTimersByTimeAsync(0);
    expect(apiStream).toHaveBeenCalledTimes(1);
    abort.abort();
    const result = await pending;
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toMatchObject({ name: "AbortError" });
    }
    await vi.advanceTimersByTimeAsync(60_000);
    expect(apiStream).toHaveBeenCalledTimes(1);
  });

  it("keeps a healthy stage across heartbeats until the first answer frame", async () => {
    vi.useFakeTimers();
    let send = (_frame: string): void => {};
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        send = (frame) => {
          controller.enqueue(encoder.encode(frame));
        };
      },
    });
    vi.mocked(apiStream).mockResolvedValue(
      new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    );
    const seen: string[] = [];
    const pending = askQuestionStream("How many students do I teach?", {
      requestId: REQUEST_ID,
      onProgress: (progress) => {
        seen.push(progress.stage);
      },
      onAnswerDelta: (text) => {
        seen.push(text);
      },
    });

    await vi.advanceTimersByTimeAsync(0);
    send(progressFrame("summarising", "running", 1, "Writing the answer"));
    await vi.advanceTimersByTimeAsync(0);
    expect(seen).toEqual(["summarising"]);

    // The audit runs before any table or answer_delta. Heartbeats are 15s and the
    // stall window is 45s, so a quiet healthy socket stays on this stage.
    await vi.advanceTimersByTimeAsync(40_000);
    send(": heartbeat\n\n");
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(40_000);
    send(": heartbeat\n\n");
    await vi.advanceTimersByTimeAsync(0);

    expect(apiStream).toHaveBeenCalledTimes(1);
    expect(seen).toEqual(["summarising"]);

    send('event: answer_delta\ndata: {"text":"You teach 10."}\n\n');
    send(finalFrame());
    await vi.advanceTimersByTimeAsync(0);
    const result = await pending;
    expect(result).toMatchObject({ kind: "final", requestId: REQUEST_ID });
    expect(seen).toEqual(["summarising", "You teach 10."]);
    expect(apiStream).toHaveBeenCalledTimes(1);
  });

  it("does not retry a missing stream endpoint", async () => {
    vi.useFakeTimers();
    vi.mocked(apiStream).mockRejectedValue(new ApiError("Not Found", 404, null));
    const pending = settle(askQuestionStream("How many?", { requestId: REQUEST_ID }));
    await vi.advanceTimersByTimeAsync(0);
    const result = await pending;
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toMatchObject({ status: 404 });
    }
    await vi.advanceTimersByTimeAsync(60_000);
    expect(apiStream).toHaveBeenCalledTimes(1);
  });
});
