import { afterEach, describe, expect, it, vi } from "vitest";

import { clearTokens, getAccessToken, getRefreshToken, setTokens } from "./client";
import { progressReconnectDelayMs, runSubject, subscribeProgress } from "./progress";

type FetchCall = {
  url: string;
  method: string;
  authorization: string | null;
  lastEventId: string | null;
  body: string | null;
};

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

describe("progress reconnect token refresh", () => {
  afterEach(() => {
    clearTokens();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("refreshes a 401 on reconnect and resumes with the same Last-Event-ID", async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);
    setTokens({
      access_token: "old-access",
      refresh_token: "refresh-1",
      token_type: "bearer",
    });

    const calls: FetchCall[] = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({
        url,
        method: init?.method ?? "GET",
        authorization: headerValue(init?.headers, "authorization"),
        lastEventId: headerValue(init?.headers, "last-event-id"),
        body: typeof init?.body === "string" ? init.body : null,
      });
      if (url.endsWith("/api/v1/auth/refresh")) {
        return jsonResponse(200, {
          access_token: "new-access",
          refresh_token: "refresh-2",
          token_type: "bearer",
        });
      }
      const progressCalls = calls.filter((call) => call.url.endsWith("/api/v1/progress/runs/run-1"));
      if (progressCalls.length === 1) {
        return sseResponse([
          'id: run-1:abc\nevent: snapshot\ndata: {"id":"run-1","phase":"indexing"}\n\n',
        ]);
      }
      if (progressCalls.length === 2) {
        return jsonResponse(401, { detail: "token expired" });
      }
      if (progressCalls.length === 3) {
        return sseResponse([
          'id: run-1:def\nevent: snapshot\ndata: {"id":"run-1","phase":"outlining"}\n\n',
          'event: close\ndata: {"reason":"terminal"}\n\n',
        ]);
      }
      throw new Error(`unexpected fetch ${url}`);
    });

    const phases: string[] = [];
    const pending = subscribeProgress(runSubject("run-1"), {
      onSnapshot: (snapshot) => {
        if ("phase" in snapshot) phases.push(snapshot.phase);
      },
    });

    await vi.advanceTimersByTimeAsync(progressReconnectDelayMs(0, 0));
    await pending;

    const progress = calls.filter((call) => call.url.endsWith("/api/v1/progress/runs/run-1"));
    const refresh = calls.filter((call) => call.url.endsWith("/api/v1/auth/refresh"));
    expect(progress).toHaveLength(3);
    expect(refresh).toHaveLength(1);
    expect(progress[0]).toMatchObject({
      method: "GET",
      authorization: "Bearer old-access",
      lastEventId: null,
    });
    expect(progress[1]).toMatchObject({
      method: "GET",
      authorization: "Bearer old-access",
      lastEventId: "run-1:abc",
    });
    expect(refresh[0]).toMatchObject({ method: "POST" });
    expect(JSON.parse(refresh[0]?.body ?? "")).toEqual({ refresh_token: "refresh-1" });
    expect(progress[2]).toMatchObject({
      method: "GET",
      authorization: "Bearer new-access",
      lastEventId: "run-1:abc",
    });
    expect(phases).toEqual(["indexing", "outlining"]);
  });

  it("aborts a token refresh without leaving tokens half-updated", async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);
    setTokens({
      access_token: "old-access",
      refresh_token: "refresh-1",
      token_type: "bearer",
    });

    const abort = new AbortController();
    let progressCount = 0;
    let refreshCount = 0;
    let releaseRefresh = false;
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/auth/refresh")) {
        refreshCount += 1;
        if (!releaseRefresh) {
          return new Promise((_resolve, reject) => {
            const signal = init?.signal;
            if (signal?.aborted) {
              reject(new DOMException("Aborted", "AbortError"));
              return;
            }
            signal?.addEventListener(
              "abort",
              () => {
                reject(new DOMException("Aborted", "AbortError"));
              },
              { once: true },
            );
          });
        }
        return jsonResponse(200, {
          access_token: "new-access",
          refresh_token: "refresh-2",
          token_type: "bearer",
        });
      }
      progressCount += 1;
      if (progressCount === 1) {
        return sseResponse([
          'id: run-1:abc\nevent: snapshot\ndata: {"id":"run-1","phase":"indexing"}\n\n',
        ]);
      }
      if (progressCount === 2 || progressCount === 3) {
        return jsonResponse(401, { detail: "token expired" });
      }
      return sseResponse([
        'id: run-1:def\nevent: snapshot\ndata: {"id":"run-1","phase":"outlining"}\n\n',
        'event: close\ndata: {"reason":"terminal"}\n\n',
      ]);
    });

    const pending = subscribeProgress(runSubject("run-1"), {
      signal: abort.signal,
      onSnapshot: () => undefined,
    }).then(
      () => ({ ok: true as const }),
      (error: unknown) => ({ ok: false as const, error }),
    );

    await vi.advanceTimersByTimeAsync(progressReconnectDelayMs(0, 0));
    expect(progressCount).toBe(2);
    expect(refreshCount).toBe(1);
    expect(getAccessToken()).toBe("old-access");
    expect(getRefreshToken()).toBe("refresh-1");

    abort.abort();
    const result = await pending;
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toMatchObject({ name: "AbortError" });
    }
    expect(getAccessToken()).toBe("old-access");
    expect(getRefreshToken()).toBe("refresh-1");
    expect(progressCount).toBe(2);

    releaseRefresh = true;
    const phases: string[] = [];
    const resumed = subscribeProgress(runSubject("run-1"), {
      onSnapshot: (snapshot) => {
        if ("phase" in snapshot) phases.push(snapshot.phase);
      },
    });
    await vi.advanceTimersByTimeAsync(0);
    await resumed;
    expect(refreshCount).toBe(2);
    expect(getAccessToken()).toBe("new-access");
    expect(getRefreshToken()).toBe("refresh-2");
    expect(phases).toEqual(["outlining"]);
    expect(vi.getTimerCount()).toBe(0);
  });
});
