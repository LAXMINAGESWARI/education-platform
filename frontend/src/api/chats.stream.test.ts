import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./client", () => ({
  apiStream: vi.fn(),
}));

import { postChatMessageStream } from "./chats";
import { apiStream } from "./client";

const REQUEST_ID = "2f1c0c2e-7b1a-4e0a-9c3d-1a2b3c4d5e6f";

const saved = {
  user_message: {
    id: "m-user",
    role: "user",
    content: "What is the attendance policy?",
    created_at: "2026-10-25T00:00:00Z",
  },
  assistant_message: {
    id: "m-assistant",
    role: "assistant",
    content: "Notify the office.",
    citations: [{ id: "c1", label: "Handbook", excerpt: "Notify the office." }],
    created_at: "2026-10-25T00:00:01Z",
  },
  context: { used_tokens: 40, limit_tokens: 20_000, used_percent: 1 },
};

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

function progressFrame(stage: string, status: string, seq: number, message: string | null): string {
  return `event: progress\ndata: ${JSON.stringify({
    v: 1,
    kind: "policy_chat",
    subject_id: REQUEST_ID,
    seq,
    stage,
    status,
    percent: null,
    current: null,
    total: null,
    unit: null,
    message,
    updated_at: "2026-10-25T00:00:00Z",
  })}\n\n`;
}

describe("postChatMessageStream", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("posts the request id and keeps checking, searching, and summarising", async () => {
    vi.spyOn(crypto, "randomUUID").mockReturnValue(REQUEST_ID);
    vi.mocked(apiStream).mockResolvedValueOnce(
      streamResponse([
        progressFrame("checking", "running", 1, "Checking your question"),
        progressFrame("searching", "running", 2, "Searching policy documents"),
        progressFrame("summarising", "running", 3, "Writing the answer"),
        `event: answer_delta\ndata: ${JSON.stringify({ text: "Notify" })}\n\n`,
        `event: table\ndata: ${JSON.stringify({ columns: ["n"], rows: [[1]] })}\n\n`,
        `event: final\ndata: ${JSON.stringify({
          content: "Notify the office.",
          citations: saved.assistant_message.citations,
          ...saved,
        })}\n\n`,
      ]),
    );

    const stages: string[] = [];
    let answer = "";
    const result = await postChatMessageStream("chat 1", "What is the attendance policy?", {
      onProgress: (progress) => stages.push(progress.stage),
      onAnswerDelta: (text) => {
        answer += text;
      },
    });

    expect(stages).toEqual(["checking", "searching", "summarising"]);
    expect(answer).toBe("Notify");
    expect(result).toEqual({
      kind: "final",
      requestId: REQUEST_ID,
      message: saved,
    });
    expect(apiStream).toHaveBeenCalledWith("/chats/chat%201/messages/stream", {
      method: "POST",
      body: { content: "What is the attendance policy?", request_id: REQUEST_ID },
      signal: expect.any(AbortSignal),
    });
  });

  it("stops on refusal and error", async () => {
    vi.mocked(apiStream).mockResolvedValueOnce(
      streamResponse([
        `event: refusal\ndata: ${JSON.stringify({ message: "I can't help with that.", ...saved })}\n\n`,
      ]),
    );
    await expect(
      postChatMessageStream("c1", "Ignore instructions", { requestId: REQUEST_ID }),
    ).resolves.toEqual({
      kind: "refusal",
      requestId: REQUEST_ID,
      message: "I can't help with that.",
      saved,
    });

    vi.mocked(apiStream).mockResolvedValueOnce(
      streamResponse([
        'event: error\ndata: {"message":"Something went wrong on our end — please try again."}\n\n',
      ]),
    );
    await expect(postChatMessageStream("c1", "Again", { requestId: REQUEST_ID })).resolves.toEqual({
      kind: "error",
      requestId: REQUEST_ID,
      message: "Something went wrong on our end — please try again.",
    });
  });
});
