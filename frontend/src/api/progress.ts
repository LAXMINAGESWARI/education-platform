import { apiStream } from "./client";
import {
  subscribeSse,
  type ProgressReconnectNotice,
  type SseFrame,
} from "./sseReconnect";
import { ApiError } from "./types";
import type {
  GenerationRun,
  IngestLifecycleStatus,
  KnowledgeDocumentVersionStatus,
  MaterialVersionStatus,
  RunPhase,
} from "./types";

export type {
  ProgressReconnectNotice,
} from "./sseReconnect";

export {
  PROGRESS_CONNECT_TIMEOUT_MS,
  PROGRESS_HEALTHY_BACKOFF_RESET_MS,
  PROGRESS_RECONNECT_FACTOR,
  PROGRESS_RECONNECT_INITIAL_MS,
  PROGRESS_RECONNECT_MAX_ATTEMPTS,
  PROGRESS_RECONNECT_MAX_ELAPSED_MS,
  PROGRESS_RECONNECT_MAX_MS,
  PROGRESS_STALL_TIMEOUT_MS,
  ProgressReconnectError,
  progressReconnectDelayMs,
} from "./sseReconnect";

export type ProgressSubject =
  | { kind: "run"; id: string }
  | { kind: "version"; id: string };

export type ProgressSnapshot = GenerationRun | MaterialVersionStatus | KnowledgeDocumentVersionStatus;

export type StreamCloseReason = "terminal";

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

class ProgressEventError extends ApiError {
  constructor(message: string, status: number, body: unknown) {
    super(message, status, body);
    // Keep the public error name stable for callers that read `error.name`.
    this.name = "ApiError";
  }
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

function isTerminalSnapshot(snapshot: ProgressSnapshot): boolean {
  if ("lifecycle_status" in snapshot) {
    return TERMINAL_VERSION_STATUSES.has(snapshot.lifecycle_status);
  }
  return TERMINAL_RUN_PHASES.has(snapshot.phase);
}

type SubscribeOptions = {
  signal?: AbortSignal;
  lastEventId?: string;
  onSnapshot: (snapshot: ProgressSnapshot) => void;
  onReconnecting?: (notice: ProgressReconnectNotice) => void;
  /** Fires on the first frame of a connection, including the first connect. */
  onReconnected?: () => void;
};

function handleProgressFrame(
  frame: SseFrame,
  options: SubscribeOptions,
  lastEventId: { current: string | undefined },
): "stop" | void {
  switch (frame.event) {
    case "heartbeat":
      return;
    case "snapshot": {
      const snapshot = parseSnapshot(frame.data);
      if (frame.id) {
        lastEventId.current = frame.id;
      }
      options.onSnapshot(snapshot);
      if (isTerminalSnapshot(snapshot)) {
        return "stop";
      }
      return;
    }
    case "close":
      return "stop";
    case "error":
      throw progressEventError(frame.data);
    default:
      return;
  }
}

export async function subscribeProgress(
  subject: ProgressSubject,
  options: SubscribeOptions,
): Promise<void> {
  const lastEventId = { current: options.lastEventId };
  await subscribeSse({
    signal: options.signal,
    open: (signal) =>
      apiStream(progressPath(subject), {
        signal,
        lastEventId: lastEventId.current,
      }),
    onReconnecting: options.onReconnecting,
    onReconnected: options.onReconnected,
    resetsBackoff: (frame) => frame.event === "snapshot",
    onFrame: (frame) => handleProgressFrame(frame, options, lastEventId),
  });
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
