/** Shared progress envelope used by text-to-SQL and upload progress streams. */

export type ProgressEnvelope = {
  v: 1;
  kind: string;
  subject_id: string;
  seq: number;
  stage: string;
  status: string;
  percent: number | null;
  current: number | null;
  total: number | null;
  unit: string | null;
  message: string | null;
  updated_at: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function nullableNumber(value: unknown, field: string): number | null {
  if (value === null) return null;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  throw new SyntaxError(`Progress envelope ${field} was not a number or null`);
}

function nullableString(value: unknown, field: string): string | null {
  if (value === null) return null;
  if (typeof value === "string") return value;
  throw new SyntaxError(`Progress envelope ${field} was not a string or null`);
}

export function parseProgressEnvelope(data: string): ProgressEnvelope {
  let parsed: unknown;
  try {
    parsed = JSON.parse(data) as unknown;
  } catch {
    throw new SyntaxError("Progress envelope was not JSON");
  }
  if (!isRecord(parsed)) {
    throw new SyntaxError("Progress envelope was not an object");
  }
  if (parsed.v !== 1) {
    throw new SyntaxError("Progress envelope version was not 1");
  }
  if (typeof parsed.kind !== "string" || parsed.kind.length === 0) {
    throw new SyntaxError("Progress envelope kind was missing");
  }
  if (typeof parsed.subject_id !== "string" || parsed.subject_id.length === 0) {
    throw new SyntaxError("Progress envelope subject_id was missing");
  }
  if (typeof parsed.seq !== "number" || !Number.isInteger(parsed.seq) || parsed.seq < 1) {
    throw new SyntaxError("Progress envelope seq was not a positive integer");
  }
  if (typeof parsed.stage !== "string" || parsed.stage.length === 0) {
    throw new SyntaxError("Progress envelope stage was missing");
  }
  if (typeof parsed.status !== "string" || parsed.status.length === 0) {
    throw new SyntaxError("Progress envelope status was missing");
  }
  if (typeof parsed.updated_at !== "string" || parsed.updated_at.length === 0) {
    throw new SyntaxError("Progress envelope updated_at was missing");
  }
  return {
    v: 1,
    kind: parsed.kind,
    subject_id: parsed.subject_id,
    seq: parsed.seq,
    stage: parsed.stage,
    status: parsed.status,
    percent: nullableNumber(parsed.percent, "percent"),
    current: nullableNumber(parsed.current, "current"),
    total: nullableNumber(parsed.total, "total"),
    unit: nullableString(parsed.unit, "unit"),
    message: nullableString(parsed.message, "message"),
    updated_at: parsed.updated_at,
  };
}
