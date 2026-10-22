import { apiStream } from "./client";
import { ApiError } from "./types";
import type {
  GenerationRun,
  IngestLifecycleStatus,
  KnowledgeDocumentVersionStatus,
  MaterialVersionStatus,
  RunPhase,
} from "./types";

export type ProgressSubject =
  | { kind: "run"; id: string }
  | { kind: "version"; id: string };

export type ProgressSnapshot = GenerationRun | MaterialVersionStatus | KnowledgeDocumentVersionStatus;

export type StreamCloseReason = "terminal";

export const PROGRESS_RECONNECT_INITIAL_MS = 1_000;

export const PROGRESS_RECONNECT_MAX_MS = 30_000;

export const PROGRESS_RECONNECT_FACTOR = 2;

/** Failed tries since the last healthy frame before the subscription rejects. */
export const PROGRESS_RECONNECT_MAX_ATTEMPTS = 8;

/** Reject when the next wait would push this outage past one minute. */
export const PROGRESS_RECONNECT_MAX_ELAPSED_MS = 60_000;

/**
 * How long one progress connect may sit before it counts as a retryable failure.
 * A Last-Event-ID resume holds the response headers until the first frame, and the
 * server heartbeat is 15s, so a quiet healthy resume can take that long. 20s clears
 * that wait without letting one hung socket consume the whole minute-long outage budget.
 */
export const PROGRESS_CONNECT_TIMEOUT_MS = 20_000;

/**
 * Reset the backoff exponent after a connection has stayed up this long.
 * Heartbeats keep the outage clock clear, but without this a quiet job would
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

const RETRYABLE_HTTP_STATUS = new Set([408, 429, 500, 502, 503, 504]);

const TERMINAL_RUN_PHASES = new Set<RunPhase>(["published", "failed", "discarded"]);

const TERMINAL_VERSION_STATUSES = new Set<IngestLifecycleStatus>([
  "ready",
  "failed",
  "published",
  "superseded",
  "archived",
]);

export function runSubject(id: string): ProgressSubject {
  return { kind: "run", id };
}

export function versionSubject(id: string): ProgressSubject {
  return { kind: "version", id };
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

function progressPath(subject: ProgressSubject): string {
  switch (subject.kind) {
    case "run":
      return `/progress/runs/${encodeURIComponent(subject.id)}`;
    case "version":
      return `/progress/versions/${encodeURIComponent(subject.id)}`;
    default: {
      const _never: never = subject;
      return _never;
    }
  }
}

type SseEvent = {
  event: string;
  data: string;
  id?: string;
};

class ProgressEventError extends ApiError {
  constructor(message: string, status: number, body: unknown) {
    super(message, status, body);
    // Keep the public error name stable for callers that read `error.name`.
    this.name = "ApiError";
  }
}

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

class ProgressStallError extends Error {
  constructor() {
    super("Progress stream stalled");
    this.name = "ProgressStallError";
  }
}

class ProgressHandlerError extends Error {
  readonly causeError: unknown;

  constructor(causeError: unknown) {
    super("Progress snapshot handler failed");
    this.name = "ProgressHandlerError";
    this.causeError = causeError;
  }
}

function abortError(): DOMException {
  return new DOMException("Aborted", "AbortError");
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function isRetryableStreamError(error: unknown): boolean {
  if (
    isAbortError(error) ||
    error instanceof ProgressEventError ||
    error instanceof ProgressHandlerError
  ) {
    return false;
  }
  if (error instanceof SyntaxError) {
    return false;
  }
  if (error instanceof ProgressStallError) {
    return true;
  }
  if (error instanceof ApiError) {
    return RETRYABLE_HTTP_STATUS.has(error.status);
  }
  return error instanceof Error;
}

function isTerminalSnapshot(snapshot: ProgressSnapshot): boolean {
  if ("lifecycle_status" in snapshot) {
    return TERMINAL_VERSION_STATUSES.has(snapshot.lifecycle_status);
  }
  return TERMINAL_RUN_PHASES.has(snapshot.phase);
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
): Promise<ReadableStreamReadResult<Uint8Array>> {
  const pending = reader.read();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new ProgressStallError());
      void reader.cancel().catch(() => undefined);
    }, PROGRESS_STALL_TIMEOUT_MS);
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

async function* readSse(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<SseEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      if (signal?.aborted) {
        throw abortError();
      }
      const { done, value } = await readWithStall(reader);
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

function parseSseBlock(block: string): SseEvent | null {
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

function progressEventError(data: string): ProgressEventError {
  try {
    const parsed: unknown = JSON.parse(data);
    if (typeof parsed === "object" && parsed !== null) {
      const payload = parsed as { detail?: unknown; status?: unknown };
      const detail = typeof payload.detail === "string" ? payload.detail : "Progress stream failed";
      const status = typeof payload.status === "number" ? payload.status : 500;
      return new ProgressEventError(detail, status, parsed);
    }
  } catch {
    // A broken error frame is still a terminal server message, not a dropped socket.
  }
  return new ProgressEventError("Progress stream failed", 500, data);
}

function parseSnapshot(data: string): ProgressSnapshot {
  const parsed: unknown = JSON.parse(data);
  if (typeof parsed !== "object" || parsed === null) {
    throw new SyntaxError("Progress snapshot was not an object");
  }
  return parsed as ProgressSnapshot;
}

function notifyReconnected(onReconnected: (() => void) | undefined): void {
  try {
    onReconnected?.();
  } catch (error) {
    throw new ProgressHandlerError(error);
  }
}

function progressAttempt(parent: AbortSignal | undefined): {
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
  }, PROGRESS_CONNECT_TIMEOUT_MS);

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

export async function subscribeProgress(
  subject: ProgressSubject,
  options: {
    signal?: AbortSignal;
    lastEventId?: string;
    onSnapshot: (snapshot: ProgressSnapshot) => void;
    onReconnecting?: (notice: ProgressReconnectNotice) => void;
    /** Fires on the first frame of a connection, including the first connect. */
    onReconnected?: () => void;
  },
): Promise<void> {
  let lastEventId = options.lastEventId;
  let attempt = 0;
  let failures = 0;
  let outageStartedAt: number | null = null;

  while (!options.signal?.aborted) {
    let disconnected: unknown;
    let healthyReset: ReturnType<typeof setTimeout> | undefined;
    const attemptSignal = progressAttempt(options.signal);
    try {
      let res: Response;
      try {
        res = await apiStream(progressPath(subject), {
          signal: attemptSignal.signal,
          lastEventId,
        });
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
      for await (const item of readSse(res.body, options.signal)) {
        if (!connectionHealthy) {
          // Headers alone are not healthy. The first frame ends the outage and
          // clears the reconnecting banner. The backoff exponent waits out
          // PROGRESS_HEALTHY_BACKOFF_RESET_MS so a one-frame proxy cannot spin.
          connectionHealthy = true;
          failures = 0;
          outageStartedAt = null;
          healthyReset = setTimeout(() => {
            healthyReset = undefined;
            attempt = 0;
          }, PROGRESS_HEALTHY_BACKOFF_RESET_MS);
          notifyReconnected(options.onReconnected);
        }
        switch (item.event) {
          case "heartbeat":
            break;
          case "snapshot": {
            const snapshot = parseSnapshot(item.data);
            if (item.id) {
              lastEventId = item.id;
            }
            try {
              options.onSnapshot(snapshot);
            } catch (error) {
              throw new ProgressHandlerError(error);
            }
            attempt = 0;
            failures = 0;
            outageStartedAt = null;
            if (isTerminalSnapshot(snapshot)) {
              return;
            }
            break;
          }
          case "close":
            return;
          case "error":
            throw progressEventError(item.data);
          default:
            break;
        }
      }
      disconnected = new TypeError("Progress stream ended");
    } catch (error) {
      if (error instanceof ProgressHandlerError) {
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

export async function watchProgress(
  subject: ProgressSubject,
  options: {
    signal?: AbortSignal;
    onSnapshot?: (snapshot: ProgressSnapshot) => void;
    onReconnecting?: (notice: ProgressReconnectNotice) => void;
    /** Fires on the first frame of a connection, including the first connect. */
    onReconnected?: () => void;
  } = {},
): Promise<ProgressSnapshot> {
  let last: ProgressSnapshot | undefined;
  await subscribeProgress(subject, {
    signal: options.signal,
    onReconnecting: options.onReconnecting,
    onReconnected: options.onReconnected,
    onSnapshot: (snapshot) => {
      last = snapshot;
      options.onSnapshot?.(snapshot);
    },
  });
  if (last === undefined) {
    throw new ApiError("Progress stream closed without a snapshot", 502, null);
  }
  return last;
}
