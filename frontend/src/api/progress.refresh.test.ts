import { afterEach, describe, expect, it, vi } from "vitest";

import {
  REFRESH_TIMEOUT_MS,
  apiRequest,
  clearTokens,
  getAccessToken,
  getRefreshToken,
  setTokens,
} from "./client";
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

  it("saves a rotated token pair when the caller aborts after the server responds", async () => {
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
    let resolveRefresh: ((response: Response) => void) | undefined;
    const authorizations: Array<string | null> = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/auth/refresh")) {
        refreshCount += 1;
        expect(init?.signal).toBeInstanceOf(AbortSignal);
        expect(init?.signal).not.toBe(abort.signal);
        return new Promise<Response>((resolve) => {
          resolveRefresh = resolve;
        });
      }
      progressCount += 1;
      authorizations.push(headerValue(init?.headers, "authorization"));
      if (progressCount === 1) {
        return sseResponse([
          'id: run-1:abc\nevent: snapshot\ndata: {"id":"run-1","phase":"indexing"}\n\n',
        ]);
      }
      if (progressCount === 2) {
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

    resolveRefresh?.(
      jsonResponse(200, {
        access_token: "new-access",
        refresh_token: "refresh-2",
        token_type: "bearer",
      }),
    );
    abort.abort();
    const result = await pending;
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toMatchObject({ name: "AbortError" });
    }
    await vi.advanceTimersByTimeAsync(0);
    expect(getAccessToken()).toBe("new-access");
    expect(getRefreshToken()).toBe("refresh-2");

    const phases: string[] = [];
    const resumed = subscribeProgress(runSubject("run-1"), {
      onSnapshot: (snapshot) => {
        if ("phase" in snapshot) phases.push(snapshot.phase);
      },
    });
    await vi.advanceTimersByTimeAsync(0);
    await resumed;
    expect(refreshCount).toBe(1);
    expect(authorizations.at(-1)).toBe("Bearer new-access");
    expect(phases).toEqual(["outlining"]);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps refreshing for the other waiter when the first caller aborts", async () => {
    setTokens({
      access_token: "old-access",
      refresh_token: "refresh-1",
      token_type: "bearer",
    });

    const abort = new AbortController();
    let refreshCount = 0;
    let resolveRefresh: ((response: Response) => void) | undefined;
    const retries: string[] = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const authorization = headerValue(init?.headers, "authorization");
      if (url.endsWith("/api/v1/auth/refresh")) {
        refreshCount += 1;
        expect(init?.signal).toBeInstanceOf(AbortSignal);
        expect(init?.signal).not.toBe(abort.signal);
        return new Promise<Response>((resolve) => {
          resolveRefresh = resolve;
        });
      }
      if (authorization === "Bearer old-access") {
        return jsonResponse(401, { detail: "token expired" });
      }
      retries.push(url);
      return jsonResponse(200, { ok: true, url });
    });

    const first = apiRequest<{ ok: boolean }>("/widgets/1", { signal: abort.signal });
    const second = apiRequest<{ ok: boolean }>("/widgets/2");
    await vi.waitUntil(() => refreshCount === 1);
    abort.abort();
    resolveRefresh?.(
      jsonResponse(200, {
        access_token: "new-access",
        refresh_token: "refresh-2",
        token_type: "bearer",
      }),
    );

    await expect(first).rejects.toMatchObject({ name: "AbortError" });
    await expect(second).resolves.toMatchObject({ ok: true });
    expect(refreshCount).toBe(1);
    expect(getAccessToken()).toBe("new-access");
    expect(getRefreshToken()).toBe("refresh-2");
    expect(retries.some((url) => url.endsWith("/api/v1/widgets/2"))).toBe(true);
  });

  it("clears tokens when the refresh itself fails", async () => {
    setTokens({
      access_token: "old-access",
      refresh_token: "refresh-1",
      token_type: "bearer",
    });
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/auth/refresh")) {
        return jsonResponse(401, { detail: "invalid refresh" });
      }
      return jsonResponse(401, { detail: "token expired" });
    });

    await expect(apiRequest("/widgets/1")).rejects.toMatchObject({ status: 401 });
    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
  });

  it("times out a hung refresh without clearing tokens, then a later refresh can run", async () => {
    vi.useFakeTimers();
    setTokens({
      access_token: "old-access",
      refresh_token: "refresh-1",
      token_type: "bearer",
    });
    let refreshCount = 0;
    vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/auth/refresh")) {
        refreshCount += 1;
        expect(init?.signal).toBeInstanceOf(AbortSignal);
        if (refreshCount === 1) {
          return new Promise<Response>((_resolve, reject) => {
            const onAbort = () => {
              reject(new DOMException("The operation was aborted.", "AbortError"));
            };
            if (init?.signal?.aborted) {
              onAbort();
              return;
            }
            init?.signal?.addEventListener("abort", onAbort, { once: true });
          });
        }
        return Promise.resolve(
          jsonResponse(200, {
            access_token: "new-access",
            refresh_token: "refresh-2",
            token_type: "bearer",
          }),
        );
      }
      const authorization = headerValue(init?.headers, "authorization");
      if (authorization === "Bearer old-access") {
        return Promise.resolve(jsonResponse(401, { detail: "token expired" }));
      }
      return Promise.resolve(jsonResponse(200, { ok: true }));
    });

    const first = apiRequest("/widgets/1");
    const firstFailed = expect(first).rejects.toMatchObject({ status: 401 });
    await vi.advanceTimersByTimeAsync(0);
    expect(refreshCount).toBe(1);
    await vi.advanceTimersByTimeAsync(REFRESH_TIMEOUT_MS);
    await firstFailed;
    expect(getAccessToken()).toBe("old-access");
    expect(getRefreshToken()).toBe("refresh-1");

    await expect(apiRequest("/widgets/1")).resolves.toMatchObject({ ok: true });
    expect(refreshCount).toBe(2);
    expect(getAccessToken()).toBe("new-access");
    expect(getRefreshToken()).toBe("refresh-2");
  });

  it.each(["network", "timeout", 503] as const)(
    "keeps stored tokens when refresh fails with %s",
    async (failure) => {
      vi.useFakeTimers();
      setTokens({
        access_token: "old-access",
        refresh_token: "refresh-1",
        token_type: "bearer",
      });
      vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/v1/auth/refresh")) {
          if (failure === "network") {
            return Promise.reject(new TypeError("network down"));
          }
          if (failure === "timeout") {
            return new Promise<Response>((_resolve, reject) => {
              init?.signal?.addEventListener(
                "abort",
                () => {
                  reject(new DOMException("The operation was aborted.", "AbortError"));
                },
                { once: true },
              );
            });
          }
          return Promise.resolve(jsonResponse(failure, { detail: "unavailable" }));
        }
        return Promise.resolve(jsonResponse(401, { detail: "token expired" }));
      });

      const pending = apiRequest("/widgets/1");
      const failed = expect(pending).rejects.toMatchObject({ status: 401 });
      if (failure === "timeout") {
        await vi.advanceTimersByTimeAsync(REFRESH_TIMEOUT_MS);
      }
      await failed;
      expect(getAccessToken()).toBe("old-access");
      expect(getRefreshToken()).toBe("refresh-1");
    },
  );

  it.each([401, 403])("clears tokens when refresh is rejected with %s", async (status) => {
    setTokens({
      access_token: "old-access",
      refresh_token: "refresh-1",
      token_type: "bearer",
    });
    vi.stubGlobal("fetch", (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/auth/refresh")) {
        return Promise.resolve(jsonResponse(status, { detail: "Invalid refresh token" }));
      }
      return Promise.resolve(jsonResponse(401, { detail: "token expired" }));
    });

    await expect(apiRequest("/widgets/1")).rejects.toMatchObject({ status: 401 });
    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
  });

  it("keeps stored tokens when a 200 refresh body is not a token pair", async () => {
    setTokens({
      access_token: "old-access",
      refresh_token: "refresh-1",
      token_type: "bearer",
    });
    vi.stubGlobal("fetch", (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/auth/refresh")) {
        return Promise.resolve(jsonResponse(200, { detail: "ok" }));
      }
      return Promise.resolve(jsonResponse(401, { detail: "token expired" }));
    });

    await expect(apiRequest("/widgets/1")).rejects.toMatchObject({ status: 401 });
    expect(getAccessToken()).toBe("old-access");
    expect(getRefreshToken()).toBe("refresh-1");
  });
});
