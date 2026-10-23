import { afterEach, describe, expect, it, vi } from "vitest";

import { clearTokens, setTokens } from "./client";
import { askQuestionStream } from "./textToSql";

const REQUEST_ID = "2f1c0c2e-7b1a-4e0a-9c3d-1a2b3c4d5e6f";

function headerValue(headers: HeadersInit | undefined, name: string): string | null {
  return new Headers(headers).get(name);
}

function sseResponse(chunks: string[]): Response {
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

function jsonResponse(status: number, payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("text-to-SQL stream token refresh", () => {
  afterEach(() => {
    clearTokens();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("refreshes a 401 and re-posts the same question and request id", async () => {
    vi.spyOn(crypto, "randomUUID").mockReturnValue(REQUEST_ID);
    setTokens({
      access_token: "old-access",
      refresh_token: "refresh-1",
      token_type: "bearer",
    });
    const calls: Array<{ url: string; method: string; authorization: string | null; body: string | null; lastEventId: string | null }> = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({
        url,
        method: init?.method ?? "GET",
        authorization: headerValue(init?.headers, "authorization"),
        body: typeof init?.body === "string" ? init.body : null,
        lastEventId: headerValue(init?.headers, "last-event-id"),
      });
      if (url.endsWith("/api/v1/auth/refresh")) {
        return jsonResponse(200, {
          access_token: "new-access",
          refresh_token: "refresh-2",
          token_type: "bearer",
        });
      }
      const asks = calls.filter((call) => call.url.endsWith("/api/v1/text-to-sql/ask/stream"));
      if (asks.length === 1) {
        return jsonResponse(401, { detail: "token expired" });
      }
      return sseResponse([
        'event: final\ndata: {"natural_answer":"You teach 10.","confidence":"high","provenance":null}\n\n',
      ]);
    });

    const result = await askQuestionStream("How many students do I teach?");
    expect(result).toMatchObject({
      kind: "final",
      requestId: REQUEST_ID,
      answer: { natural_answer: "You teach 10.", confidence: "high" },
    });
    const asks = calls.filter((call) => call.url.endsWith("/api/v1/text-to-sql/ask/stream"));
    expect(asks).toHaveLength(2);
    expect(asks[0]).toMatchObject({
      method: "POST",
      authorization: "Bearer old-access",
      lastEventId: null,
      body: JSON.stringify({ question: "How many students do I teach?", request_id: REQUEST_ID }),
    });
    expect(asks[1]).toMatchObject({
      method: "POST",
      authorization: "Bearer new-access",
      lastEventId: null,
      body: JSON.stringify({ question: "How many students do I teach?", request_id: REQUEST_ID }),
    });
  });
});
