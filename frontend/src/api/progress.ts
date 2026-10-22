import { apiStream } from "./client";
import { parseProgressEnvelope, type ProgressEnvelope } from "./progressEnvelope";
import {
  subscribeSse,
  type ProgressReconnectNotice,
  type SseFrame,
} from "./sseReconnect";
import { noteProgressSeq, resetProgressCursor } from "./stageOrder";
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
  onProgress?: (progress: ProgressEnvelope) => void;
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
  let cursor = resetProgressCursor();
  await subscribeSse({
    signal: options.signal,
    open: (signal) =>
      apiStream(progressPath(subject), {
        signal,
        lastEventId: lastEventId.current,
      }),
    onReconnecting: (notice) => {
      cursor = resetProgressCursor();
      options.onReconnecting?.(notice);
    },
    onReconnected: options.onReconnected,
    resetsBackoff: (frame) => frame.event === "snapshot",
    onFrame: (frame) => {
      if (frame.event !== "progress") {
        return handleProgressFrame(frame, options, lastEventId);
      }
      let envelope: ProgressEnvelope;
      try {
        envelope = parseProgressEnvelope(frame.data);
      } catch (error) {
        if (error instanceof SyntaxError) return;
        throw error;
      }
      const noted = noteProgressSeq(cursor, envelope.seq);
      cursor = noted.cursor;
      if (noted.accept) options.onProgress?.(envelope);
    },
  });
}


export async function watchProgress(
  subject: ProgressSubject,
  options: {
    signal?: AbortSignal;
    onSnapshot?: (snapshot: ProgressSnapshot) => void;
    onProgress?: (progress: ProgressEnvelope) => void;
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
    onProgress: options.onProgress,
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

export type ProgressKind = "upload" | "generation" | "text_to_sql" | "policy_chat";

export type ProgressStatus = "running" | "done" | "failed";

export type ProgressUnit = "page" | "chunk";

export type UploadStage =
  | "received"
  | "parsing"
  | "chunking"
  | "embedding"
  | "extracting_topics"
  | "done"
  | "failed";

export type GenerationStage =
  | "indexing"
  | "outlining"
  | "outline_review"
  | "generating"
  | "qa_review"
  | "published"
  | "failed"
  | "discarded";

export type TextToSqlStage =
  | "checking"
  | "understanding"
  | "writing_query"
  | "validating"
  | "running"
  | "summarising";

export type PolicyChatStage = "checking" | "searching" | "summarising";

type StageProgressBase = {
  v: 1;
  subject_id: string;
  seq: number;
  status: ProgressStatus;
  percent: number | null;
  current: number | null;
  total: number | null;
  unit: ProgressUnit | null;
  message: string | null;
  updated_at: string;
};

export type UploadProgress = StageProgressBase & { kind: "upload"; stage: UploadStage };

export type GenerationProgress = StageProgressBase & { kind: "generation"; stage: GenerationStage };

export type TextToSqlProgress = StageProgressBase & { kind: "text_to_sql"; stage: TextToSqlStage };

export type PolicyChatProgress = StageProgressBase & { kind: "policy_chat"; stage: PolicyChatStage };

export type StageProgress = UploadProgress | GenerationProgress | TextToSqlProgress | PolicyChatProgress;

export type AnswerDelta = { text: string };

export type ProgressTable = { columns: string[]; rows: unknown[][] };

export type TextToSqlFinal = {
  natural_answer: string;
  confidence: string | null;
  provenance: string | null;
};

export type PolicyCitation = { id: string; label: string; excerpt: string };

export type PolicyChatFinal = { content: string; citations: PolicyCitation[] };

export type ProgressRefusal = { message: string };

/** Parsed SSE payload for the shared progress schema. The UI does not consume this yet. */
export type ProgressEvent =
  | { event: "progress"; data: StageProgress }
  | { event: "answer_delta"; data: AnswerDelta }
  | { event: "table"; data: ProgressTable }
  | { event: "final"; data: TextToSqlFinal | PolicyChatFinal }
  | { event: "refusal"; data: ProgressRefusal };

const UPLOAD_STAGES: readonly UploadStage[] = [
  "received",
  "parsing",
  "chunking",
  "embedding",
  "extracting_topics",
  "done",
  "failed",
];

const GENERATION_STAGES: readonly GenerationStage[] = [
  "indexing",
  "outlining",
  "outline_review",
  "generating",
  "qa_review",
  "published",
  "failed",
  "discarded",
];

const TEXT_TO_SQL_STAGES: readonly TextToSqlStage[] = [
  "checking",
  "understanding",
  "writing_query",
  "validating",
  "running",
  "summarising",
];

const POLICY_STAGES: readonly PolicyChatStage[] = ["checking", "searching", "summarising"];

const PROGRESS_STATUSES: readonly ProgressStatus[] = ["running", "done", "failed"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStage<T extends string>(value: unknown, allowed: readonly T[]): value is T {
  return typeof value === "string" && allowed.some((item) => item === value);
}

function stagesFor(kind: ProgressKind): readonly string[] {
  switch (kind) {
    case "upload":
      return UPLOAD_STAGES;
    case "generation":
      return GENERATION_STAGES;
    case "text_to_sql":
      return TEXT_TO_SQL_STAGES;
    case "policy_chat":
      return POLICY_STAGES;
    default: {
      const _never: never = kind;
      return _never;
    }
  }
}

function isKind(value: unknown): value is ProgressKind {
  return value === "upload" || value === "generation" || value === "text_to_sql" || value === "policy_chat";
}

function nullableInt(value: unknown): number | null | undefined {
  if (value === null) {
    return null;
  }
  if (typeof value === "number" && Number.isInteger(value)) {
    return value;
  }
  return undefined;
}

function parseUnit(value: unknown): ProgressUnit | null | undefined {
  if (value === null) {
    return null;
  }
  if (value === "page" || value === "chunk") {
    return value;
  }
  return undefined;
}

function parseStageProgress(value: Record<string, unknown>): StageProgress | null {
  if (value.v !== 1 || !isKind(value.kind) || typeof value.subject_id !== "string") {
    return null;
  }
  if (typeof value.seq !== "number" || !Number.isInteger(value.seq) || value.seq < 1) {
    return null;
  }
  if (!isStage(value.status, PROGRESS_STATUSES)) {
    return null;
  }
  if (!isStage(value.stage, stagesFor(value.kind))) {
    return null;
  }
  const percent = nullableInt(value.percent);
  const current = nullableInt(value.current);
  const total = nullableInt(value.total);
  if (percent === undefined || current === undefined || total === undefined) {
    return null;
  }
  if (percent !== null && (percent < 0 || percent > 100)) {
    return null;
  }
  const unit = parseUnit(value.unit);
  if (unit === undefined) {
    return null;
  }
  const message = value.message === null ? null : typeof value.message === "string" ? value.message : undefined;
  if (message === undefined || typeof value.updated_at !== "string") {
    return null;
  }
  const base = {
    v: 1 as const,
    subject_id: value.subject_id,
    seq: value.seq,
    status: value.status,
    percent,
    current,
    total,
    unit,
    message,
    updated_at: value.updated_at,
  };
  switch (value.kind) {
    case "upload":
      if (!isStage(value.stage, UPLOAD_STAGES)) {
        return null;
      }
      return { ...base, kind: value.kind, stage: value.stage };
    case "generation":
      if (!isStage(value.stage, GENERATION_STAGES)) {
        return null;
      }
      return { ...base, kind: value.kind, stage: value.stage };
    case "text_to_sql":
      if (!isStage(value.stage, TEXT_TO_SQL_STAGES)) {
        return null;
      }
      return { ...base, kind: value.kind, stage: value.stage };
    case "policy_chat":
      if (!isStage(value.stage, POLICY_STAGES)) {
        return null;
      }
      return { ...base, kind: value.kind, stage: value.stage };
    default: {
      const _never: never = value.kind;
      return _never;
    }
  }
}

function parseFinal(value: Record<string, unknown>): TextToSqlFinal | PolicyChatFinal | null {
  if (typeof value.natural_answer === "string") {
    const confidence = value.confidence === null ? null : value.confidence;
    const provenance = value.provenance === null ? null : value.provenance;
    if (
      (confidence === null || typeof confidence === "string") &&
      (provenance === null || typeof provenance === "string")
    ) {
      return { natural_answer: value.natural_answer, confidence, provenance };
    }
    return null;
  }
  if (typeof value.content !== "string" || !Array.isArray(value.citations)) {
    return null;
  }
  const citations: PolicyCitation[] = [];
  for (const item of value.citations) {
    if (!isRecord(item) || typeof item.id !== "string" || typeof item.label !== "string" || typeof item.excerpt !== "string") {
      return null;
    }
    citations.push({ id: item.id, label: item.label, excerpt: item.excerpt });
  }
  return { content: value.content, citations };
}

export function parseProgressEvent(eventName: string, raw: unknown): ProgressEvent | null {
  if (!isRecord(raw)) {
    return null;
  }
  switch (eventName) {
    case "progress": {
      const data = parseStageProgress(raw);
      return data === null ? null : { event: "progress", data };
    }
    case "answer_delta":
      return typeof raw.text === "string" ? { event: "answer_delta", data: { text: raw.text } } : null;
    case "table": {
      if (!Array.isArray(raw.columns) || !raw.columns.every((column) => typeof column === "string")) {
        return null;
      }
      if (!Array.isArray(raw.rows) || !raw.rows.every((row) => Array.isArray(row))) {
        return null;
      }
      return {
        event: "table",
        data: { columns: raw.columns, rows: raw.rows },
      };
    }
    case "final": {
      const data = parseFinal(raw);
      return data === null ? null : { event: "final", data };
    }
    case "refusal":
      return typeof raw.message === "string" ? { event: "refusal", data: { message: raw.message } } : null;
    default:
      return null;
  }
}
