import { ApiError } from "./types";

export const PROGRESS_RECONNECT_INITIAL_MS = 1_000;

export const PROGRESS_RECONNECT_MAX_MS = 30_000;

export const PROGRESS_RECONNECT_FACTOR = 2;

/** Failed tries since the last healthy frame before the subscription rejects. */
export const PROGRESS_RECONNECT_MAX_ATTEMPTS = 8;

/**
 * Reject once this outage has lasted one minute.
 * Connect waits, stall watches, and backoff delays all spend the same budget.
 * An in-flight wait is clamped so it cannot run past the deadline.
 */
export const PROGRESS_RECONNECT_MAX_ELAPSED_MS = 60_000;

/**
 * How long one connect may sit before it counts as a retryable failure.
 * A quiet healthy resume can take about as long as the 15s server heartbeat.
 * 20s clears that wait without letting one hung socket consume the whole minute.
 */
export const PROGRESS_CONNECT_TIMEOUT_MS = 20_000;

/**
 * Reset the backoff exponent after a connection has stayed up this long.
 * Heartbeats keep the outage clock clear, but without this a quiet stream would
 * keep the capped 30s delay and a later real outage would only get about two tries.
 */
export const PROGRESS_HEALTHY_BACKOFF_RESET_MS = PROGRESS_RECONNECT_MAX_MS;

/**
 * Drop an open stream that delivers no frame for this long.
 * The server heartbeat is 15s, so 45s is three missed beats: one late heartbeat
 * stays up, and a stalled body cannot sit open with the UI already reconnected.
 */
export const PROGRESS_STALL_TIMEOUT_MS = 45_000;

export type ProgressReconnectNotice = {
  /** 1-based count of failures in the current outage. */
  attempt: number;
  delayMs: number;
};

export type SseFrame = {
  event: string;
  data: string;
  id?: string;
};

export class ProgressReconnectError extends Error {
  readonly attempts: number;
  readonly elapsedMs: number;

  constructor(attempts: number, elapsedMs: number, cause: unknown) {
    super("Lost the connection to progress updates. Try again.", { cause });
    this.name = "ProgressReconnectError";
    this.attempts = attempts;
    this.elapsedMs = elapsedMs;
  }
}

class SseStallError extends Error {
  constructor() {
    super("Progress stream stalled");
    this.name = "ProgressStallError";
  }
}

class SseCallbackError extends Error {
  readonly causeError: unknown;

  constructor(causeError: unknown) {
    super("SSE callback failed");
    this.name = "SseCallbackError";
    this.causeError = causeError;
  }
}

const RETRYABLE_HTTP_STATUS = new Set([408, 429, 500, 502, 503, 504]);

function abortError(): DOMException {
  return new DOMException("Aborted", "AbortError");
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function isRetryableStreamError(error: unknown): boolean {
  if (isAbortError(error) || error instanceof SseCallbackError) {
    return false;
  }
  if (error instanceof SyntaxError) {
    return false;
  }
  if (error instanceof SseStallError) {
    return true;
  }
  if (error instanceof ApiError) {
    return RETRYABLE_HTTP_STATUS.has(error.status);
  }
  return error instanceof Error;
}

/** Pre-retry wait for 0-based `attempt`. `random` in [0, 1] selects [50%, 100%] of the capped delay. */
export function progressReconnectDelayMs(attempt: number, random: number): number {
  const exponent = Math.min(Math.max(0, attempt), 16);
  const exponential = Math.min(
    PROGRESS_RECONNECT_MAX_MS,
    PROGRESS_RECONNECT_INITIAL_MS * PROGRESS_RECONNECT_FACTOR ** exponent,
  );
  const unit = Math.min(1, Math.max(0, random));
  return Math.floor(exponential * (0.5 + unit * 0.5));
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) {
    return Promise.reject(abortError());
  }
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(finish, ms);
    function finish() {
      if (settled) {
        return;
      }
      settled = true;
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }
    function onAbort() {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      reject(abortError());
    }
    signal?.addEventListener("abort", onAbort);
  });
}

function readWithStall(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  timeoutMs: number,
): Promise<ReadableStreamReadResult<Uint8Array>> {
  const pending = reader.read();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new SseStallError());
      void reader.cancel().catch(() => undefined);
    }, timeoutMs);
    pending.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

export function parseSseBlock(block: string): SseFrame | null {
  let event = "message";
  let id: string | undefined;
  let sawComment = false;
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(":")) {
      sawComment = true;
      continue;
    }
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
      continue;
    }
    if (line.startsWith("id:")) {
      id = line.slice(3).trim();
      continue;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (dataLines.length === 0) {
    if (sawComment) {
      return { event: "heartbeat", data: "" };
    }
    return null;
  }
  return { event, data: dataLines.join("\n"), id };
}

async function* readSse(
  body: ReadableStream<Uint8Array>,
  signal: AbortSignal | undefined,
  stallTimeoutMs: () => number,
): AsyncGenerator<SseFrame> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      if (signal?.aborted) {
        throw abortError();
      }
      const { done, value } = await readWithStall(reader, stallTimeoutMs());
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        const parsed = parseSseBlock(block);
        if (parsed !== null) {
          yield parsed;
        }
      }
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // The stream already failed or the lock was released by the failed read.
    }
  }
}

function createAttempt(parent: AbortSignal | undefined, timeoutMs: number): {
  signal: AbortSignal;
  connected: () => void;
  finish: () => void;
  timedOut: () => boolean;
} {
  const controller = new AbortController();
  let didTimeout = false;
  let open = false;
  let finished = false;
  const timer = setTimeout(() => {
    didTimeout = true;
    if (!open) {
      controller.abort();
    }
  }, timeoutMs);

  function onParentAbort(): void {
    controller.abort();
  }

  if (parent?.aborted) {
    controller.abort();
  } else {
    parent?.addEventListener("abort", onParentAbort);
  }

  return {
    signal: controller.signal,
    timedOut: () => didTimeout && parent?.aborted !== true,
    connected: () => {
      open = true;
      clearTimeout(timer);
    },
    finish: () => {
      if (finished) {
        return;
      }
      finished = true;
      open = true;
      clearTimeout(timer);
      parent?.removeEventListener("abort", onParentAbort);
    },
  };
}

export async function subscribeSse(options: {
  signal?: AbortSignal;
  open: (signal: AbortSignal) => Promise<Response>;
  onFrame: (frame: SseFrame) => "stop" | void;
  /** Fires on the first frame of a connection, including a heartbeat. */
  onReconnected?: () => void;
  onReconnecting?: (notice: ProgressReconnectNotice) => void;
  /** Zero the backoff exponent immediately. Progress snapshots do this. */
  resetsBackoff?: (frame: SseFrame) => boolean;
}): Promise<void> {
  let attempt = 0;
  let failures = 0;
  let outageStartedAt: number | null = null;
  let disconnected: unknown;

  // The clock starts when a wait begins, and a healthy frame clears it.
  // Clamping each wait to the remainder keeps a silent body inside the minute.
  function clampedWait(capMs: number): number {
    const started = outageStartedAt ?? Date.now();
    outageStartedAt = started;
    const remaining = PROGRESS_RECONNECT_MAX_ELAPSED_MS - (Date.now() - started);
    return Math.max(0, Math.min(capMs, remaining));
  }

  while (!options.signal?.aborted) {
    if (
      outageStartedAt !== null &&
      Date.now() - outageStartedAt >= PROGRESS_RECONNECT_MAX_ELAPSED_MS
    ) {
      throw new ProgressReconnectError(failures, Date.now() - outageStartedAt, disconnected);
    }
    let healthyReset: ReturnType<typeof setTimeout> | undefined;
    const attemptSignal = createAttempt(options.signal, clampedWait(PROGRESS_CONNECT_TIMEOUT_MS));
    try {
      let res: Response;
      try {
        res = await options.open(attemptSignal.signal);
      } catch (error) {
        if (options.signal?.aborted) {
          throw abortError();
        }
        if (attemptSignal.timedOut()) {
          throw new TypeError("Progress connect timed out");
        }
        throw error;
      }
      attemptSignal.connected();
      if (res.body === null) {
        throw new ApiError("Progress stream had no body", 502, null);
      }
      let connectionHealthy = false;
      for await (const item of readSse(res.body, options.signal, () =>
        connectionHealthy ? PROGRESS_STALL_TIMEOUT_MS : clampedWait(PROGRESS_STALL_TIMEOUT_MS),
      )) {
        if (!connectionHealthy) {
          // Headers alone are not healthy. The first frame ends the outage.
          // The backoff exponent waits out PROGRESS_HEALTHY_BACKOFF_RESET_MS
          // so a one-frame proxy cannot spin.
          connectionHealthy = true;
          failures = 0;
          healthyReset = setTimeout(() => {
            healthyReset = undefined;
            attempt = 0;
          }, PROGRESS_HEALTHY_BACKOFF_RESET_MS);
          try {
            options.onReconnected?.();
          } catch (error) {
            throw new SseCallbackError(error);
          }
        }
        // This socket has delivered a frame. Quiet time after that is not an
        // outage: the next read keeps the full stall window and does not start
        // the budget. The clock starts again only once the socket drops.
        outageStartedAt = null;
        if (options.resetsBackoff?.(item) === true) {
          attempt = 0;
        }
        try {
          if (options.onFrame(item) === "stop") {
            return;
          }
        } catch (error) {
          throw new SseCallbackError(error);
        }
      }
      disconnected = new TypeError("Progress stream ended");
    } catch (error) {
      if (error instanceof SseCallbackError) {
        throw error.causeError;
      }
      if (isAbortError(error) || options.signal?.aborted) {
        throw abortError();
      }
      if (!isRetryableStreamError(error)) {
        throw error;
      }
      disconnected = error;
    } finally {
      if (healthyReset !== undefined) {
        clearTimeout(healthyReset);
      }
      attemptSignal.finish();
    }

    if (options.signal?.aborted) {
      throw abortError();
    }

    failures += 1;
    if (outageStartedAt === null) {
      outageStartedAt = Date.now();
    }
    const elapsedMs = Date.now() - outageStartedAt;
    const waitMs = progressReconnectDelayMs(attempt, Math.random());
    if (
      failures >= PROGRESS_RECONNECT_MAX_ATTEMPTS ||
      elapsedMs >= PROGRESS_RECONNECT_MAX_ELAPSED_MS ||
      elapsedMs + waitMs > PROGRESS_RECONNECT_MAX_ELAPSED_MS
    ) {
      throw new ProgressReconnectError(failures, elapsedMs, disconnected);
    }
    options.onReconnecting?.({ attempt: failures, delayMs: waitMs });
    attempt += 1;
    await sleep(waitMs, options.signal);
  }

  throw abortError();
}
