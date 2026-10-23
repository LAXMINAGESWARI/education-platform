import { apiRequest, apiStream } from "./client";
import { parseProgressEnvelope, type ProgressEnvelope } from "./progressEnvelope";
import {
  subscribeSse,
  type ProgressReconnectNotice,
  type SseFrame,
} from "./sseReconnect";
import { noteProgressSeq, resetProgressCursor, TEXT_TO_SQL_STAGE_ORDER } from "./stageOrder";

export type Confidence = "high" | "medium" | "low";

export type AskResponse = {
  natural_answer: string;
  confidence: Confidence | null;
  provenance: string | null;
};

/**
 * Single-turn: sends only the current question, never prior turns. There is no history
 * parameter on this function at all, deliberately — the backend graph has no
 * conversation-memory concept (see education_platform.modules.text_to_sql.state), and any
 * thread shown in the UI is a display concern only, built up client-side from independent
 * responses, not something fed back into subsequent requests.
 */
export function askQuestion(question: string): Promise<AskResponse> {
  return apiRequest<AskResponse>("/text-to-sql/ask", {
    method: "POST",
    auth: true,
    body: { question },
  });
}

export const TEXT_TO_SQL_STAGES = TEXT_TO_SQL_STAGE_ORDER;

export type TextToSqlStage = (typeof TEXT_TO_SQL_STAGE_ORDER)[number];

export type TextToSqlStageStatus = "running" | "done" | "failed";

export const TEXT_TO_SQL_STAGE_LABEL: Record<TextToSqlStage, string> = {
  checking: "Checking",
  understanding: "Understanding",
  writing_query: "Writing the query",
  validating: "Validating",
  running: "Running",
  summarising: "Summarising",
};

const TEXT_TO_SQL_STAGE_SET: ReadonlySet<string> = new Set(TEXT_TO_SQL_STAGES);

const TEXT_TO_SQL_STATUS_SET: ReadonlySet<string> = new Set(["running", "done", "failed"]);

export type TextToSqlProgress = ProgressEnvelope & {
  kind: "text_to_sql";
  stage: TextToSqlStage;
  status: TextToSqlStageStatus;
};

export type TextToSqlCell = string | number | boolean | null;

export type TextToSqlTable = {
  columns: string[];
  rows: TextToSqlCell[][];
};

export type TextToSqlStreamResult =
  | { kind: "final"; requestId: string; answer: AskResponse }
  | { kind: "refusal"; requestId: string; message: string }
  | { kind: "error"; requestId: string; message: string };

export type TextToSqlFrame =
  | { type: "heartbeat" }
  | { type: "progress"; progress: TextToSqlProgress }
  | { type: "answer_delta"; text: string }
  | { type: "table"; table: TextToSqlTable }
  | { type: "final"; answer: AskResponse }
  | { type: "refusal"; message: string }
  | { type: "error"; message: string }
  | { type: "ignore" };

type StreamHandlers = {
  signal?: AbortSignal;
  /** Client-generated UUID. One id is reused for every re-POST of this ask. */
  requestId?: string;
  onProgress?: (progress: TextToSqlProgress) => void;
  /**
   * Reconnect re-POSTs the same request id. The server replays the stored run
   * and does not execute SQL again. Partial answer tokens are not stored, so
   * drop them here. A finished run replays only the terminal frame.
   */
  onAnswerReset?: () => void;
  onAnswerDelta?: (text: string) => void;
  onTable?: (table: TextToSqlTable) => void;
  onReconnecting?: (notice: ProgressReconnectNotice) => void;
  onReconnected?: () => void;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseJsonObject(data: string, label: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(data) as unknown;
  } catch {
    throw new SyntaxError(`${label} was not JSON`);
  }
  if (!isRecord(parsed)) {
    throw new SyntaxError(`${label} was not an object`);
  }
  return parsed;
}

function parseConfidence(value: unknown): Confidence | null {
  if (value === null) return null;
  if (value === "high" || value === "medium" || value === "low") return value;
  throw new SyntaxError("Ask confidence was not a known value");
}

function parseCell(value: unknown): TextToSqlCell {
  if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return value;
  }
  throw new SyntaxError("Table cell was not a JSON scalar");
}

function parseTable(data: string): TextToSqlTable {
  const payload = parseJsonObject(data, "Table frame");
  if (!Array.isArray(payload.columns) || payload.columns.some((column) => typeof column !== "string")) {
    throw new SyntaxError("Table columns were not strings");
  }
  if (!Array.isArray(payload.rows)) {
    throw new SyntaxError("Table rows were missing");
  }
  const columns = payload.columns;
  const rows = payload.rows.map((row) => {
    if (!Array.isArray(row) || row.length !== columns.length) {
      throw new SyntaxError("Table row did not match the columns");
    }
    return row.map((cell) => parseCell(cell));
  });
  return { columns, rows };
}

function parseFinal(data: string): AskResponse {
  const payload = parseJsonObject(data, "Final frame");
  if (typeof payload.natural_answer !== "string") {
    throw new SyntaxError("Final frame had no natural_answer");
  }
  const provenance = payload.provenance;
  if (provenance !== null && typeof provenance !== "string") {
    throw new SyntaxError("Final frame provenance was not a string or null");
  }
  return {
    natural_answer: payload.natural_answer,
    confidence: parseConfidence(payload.confidence),
    provenance,
  };
}

function parseMessageFrame(data: string, label: string): string {
  const payload = parseJsonObject(data, label);
  if (typeof payload.message !== "string" || payload.message.length === 0) {
    throw new SyntaxError(`${label} had no message`);
  }
  return payload.message;
}

function parseTextToSqlProgress(data: string): TextToSqlProgress {
  const envelope = parseProgressEnvelope(data);
  if (envelope.kind !== "text_to_sql") {
    throw new SyntaxError("Progress frame kind was not text_to_sql");
  }
  if (!TEXT_TO_SQL_STAGE_SET.has(envelope.stage)) {
    throw new SyntaxError("Progress frame stage was not a text-to-SQL stage");
  }
  if (!TEXT_TO_SQL_STATUS_SET.has(envelope.status)) {
    throw new SyntaxError("Progress frame status was not running, done, or failed");
  }
  return {
    ...envelope,
    kind: "text_to_sql",
    stage: envelope.stage as TextToSqlStage,
    status: envelope.status as TextToSqlStageStatus,
  };
}

export function parseTextToSqlFrame(frame: SseFrame): TextToSqlFrame {
  switch (frame.event) {
    case "heartbeat":
      return { type: "heartbeat" };
    case "progress":
      return { type: "progress", progress: parseTextToSqlProgress(frame.data) };
    case "answer_delta": {
      const payload = parseJsonObject(frame.data, "Answer delta");
      if (typeof payload.text !== "string") {
        throw new SyntaxError("Answer delta had no text");
      }
      return { type: "answer_delta", text: payload.text };
    }
    case "table":
      return { type: "table", table: parseTable(frame.data) };
    case "final":
      return { type: "final", answer: parseFinal(frame.data) };
    case "refusal":
      return { type: "refusal", message: parseMessageFrame(frame.data, "Refusal") };
    case "error":
      return { type: "error", message: parseMessageFrame(frame.data, "Error") };
    default:
      return { type: "ignore" };
  }
}

function newRequestId(): string {
  return crypto.randomUUID();
}

/**
 * Stream one teacher question. A dropped connection re-POSTs the same
 * `request_id`. The server replays the stored run instead of starting over,
 * so the seq cursor resets and `onAnswerReset` fires before that next POST.
 * A progress seq lower than the last accepted one is ignored. Gaps are kept.
 * `final`, `refusal`, and `error` end the subscription.
 */
export async function askQuestionStream(
  question: string,
  handlers: StreamHandlers = {},
): Promise<TextToSqlStreamResult> {
  const requestId = handlers.requestId ?? newRequestId();
  let outcome: TextToSqlStreamResult | undefined;
  let cursor = resetProgressCursor();

  await subscribeSse({
    signal: handlers.signal,
    open: (signal) =>
      apiStream("/text-to-sql/ask/stream", {
        method: "POST",
        body: { question, request_id: requestId },
        signal,
      }),
    onReconnected: handlers.onReconnected,
    onReconnecting: (notice) => {
      cursor = resetProgressCursor();
      handlers.onAnswerReset?.();
      handlers.onReconnecting?.(notice);
    },
    onFrame: (frame) => {
      const parsed = parseTextToSqlFrame(frame);
      switch (parsed.type) {
        case "heartbeat":
        case "ignore":
          return;
        case "progress": {
          const noted = noteProgressSeq(cursor, parsed.progress.seq);
          cursor = noted.cursor;
          if (!noted.accept) return;
          handlers.onProgress?.(parsed.progress);
          return;
        }
        case "answer_delta":
          handlers.onAnswerDelta?.(parsed.text);
          return;
        case "table":
          handlers.onTable?.(parsed.table);
          return;
        case "final":
          outcome = { kind: "final", requestId, answer: parsed.answer };
          return "stop";
        case "refusal":
          outcome = { kind: "refusal", requestId, message: parsed.message };
          return "stop";
        case "error":
          outcome = { kind: "error", requestId, message: parsed.message };
          return "stop";
        default: {
          const _never: never = parsed;
          return _never;
        }
      }
    },
  });

  if (outcome === undefined) {
    throw new SyntaxError("Text-to-SQL stream ended without a final frame");
  }
  return outcome;
}
