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
    this.name = "ApiError";
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
      const { done, value } = await reader.read();
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

export async function subscribeProgress(
  subject: ProgressSubject,
  options: {
    signal?: AbortSignal;
    lastEventId?: string;
    onSnapshot: (snapshot: ProgressSnapshot) => void;
  },
): Promise<void> {
  let lastEventId = options.lastEventId;
  let attempt = 0;

  while (!options.signal?.aborted) {
    try {
      const res = await apiStream(progressPath(subject), {
        signal: options.signal,
        lastEventId,
      });
      if (res.body === null) {
        throw new ApiError("Progress stream had no body", 502, null);
      }
      for await (const item of readSse(res.body, options.signal)) {
        // A delivered frame marks this socket healthy, so the next drop starts at the base delay.
        attempt = 0;
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
    }

    if (options.signal?.aborted) {
      throw abortError();
    }

    const waitMs = progressReconnectDelayMs(attempt, Math.random());
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
  } = {},
): Promise<ProgressSnapshot> {
  let last: ProgressSnapshot | undefined;
  await subscribeProgress(subject, {
    signal: options.signal,
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
