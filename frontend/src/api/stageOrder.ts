/**
 * Ordered work stages shared by progress steppers.
 * Each progress event is the full current state. A stage is done when the
 * current stage sits later in this list, even if that earlier event never arrived.
 */

export const UPLOAD_STAGE_ORDER = [
  "received",
  "parsing",
  "chunking",
  "embedding",
  "extracting_topics",
  "done",
] as const;

export type UploadStage = (typeof UPLOAD_STAGE_ORDER)[number];

export const TEXT_TO_SQL_STAGE_ORDER = [
  "checking",
  "understanding",
  "writing_query",
  "validating",
  "running",
  "summarising",
] as const;

/** Upload `failed` is terminal and is not a work stage. Indexing it would mark `done` complete. */
export type StageStatus = "running" | "done" | "failed";

export type StageSnapshot = {
  stage: string;
  status: StageStatus;
};

export type StageVisual = "pending" | "current" | "complete" | "failed";

export type ProgressCursor = {
  seq: number | null;
};

export function stageVisuals<Stage extends string>(
  order: readonly Stage[],
  snapshot: StageSnapshot | null,
): { stage: Stage; visual: StageVisual }[] {
  const currentIndex = snapshot === null ? -1 : indexOfStage(order, snapshot.stage);
  return order.map((stage, index) => ({
    stage,
    visual: visualAt(index, currentIndex, snapshot),
  }));
}

/** A reconnect delivers the current snapshot and does not continue the previous seq. */
export function resetProgressCursor(): ProgressCursor {
  return { seq: null };
}

/**
 * Accept a seq that is not lower than the last one seen. Gaps are expected.
 * A lower seq is a stale event and is ignored until `resetProgressCursor`.
 */
export function noteProgressSeq(
  cursor: ProgressCursor,
  seq: number,
): { cursor: ProgressCursor; accept: boolean } {
  if (cursor.seq !== null && seq < cursor.seq) {
    return { cursor, accept: false };
  }
  return { cursor: { seq }, accept: true };
}

function indexOfStage<Stage extends string>(order: readonly Stage[], stage: string): number {
  for (let index = 0; index < order.length; index += 1) {
    if (order[index] === stage) return index;
  }
  return -1;
}

const TEXT_TO_SQL_PARALLEL = ["checking", "understanding"] as const;

type ParallelStage = (typeof TEXT_TO_SQL_PARALLEL)[number];

/** Latest text-to-SQL progress plus the two stages that run together. */
export type TextToSqlStageReport = {
  latest: StageSnapshot | null;
  pair: Partial<Record<ParallelStage, StageStatus>>;
};

export function emptyTextToSqlStageReport(): TextToSqlStageReport {
  return { latest: null, pair: {} };
}

/**
 * A finished ask replays only the terminal frame. Fill the stepper from that
 * outcome when no progress frame arrived with it.
 */
export function terminalTextToSqlStageReport(status: "done" | "failed"): TextToSqlStageReport {
  const stage = status === "done" ? "summarising" : "checking";
  return noteTextToSqlStage(emptyTextToSqlStageReport(), { stage, status });
}

/** Remember checking and understanding on their own. A later stage does not erase that. */
export function noteTextToSqlStage(
  report: TextToSqlStageReport,
  snapshot: StageSnapshot,
): TextToSqlStageReport {
  const stage = parallelStage(snapshot.stage);
  if (stage === null) {
    return { latest: snapshot, pair: report.pair };
  }
  return {
    latest: snapshot,
    pair: { ...report.pair, [stage]: snapshot.status },
  };
}

/**
 * Checking and understanding stay in progress together.
 * One of them is done only when that stage reports done, or a later stage arrives.
 * A later stage still marks every earlier stage done, including ones that never arrived.
 */
export function textToSqlStageVisuals(
  report: TextToSqlStageReport,
): { stage: (typeof TEXT_TO_SQL_STAGE_ORDER)[number]; visual: StageVisual }[] {
  const latest = report.latest;
  const currentIndex = latest === null ? -1 : indexOfStage(TEXT_TO_SQL_STAGE_ORDER, latest.stage);
  const parallelEnd = indexOfStage(TEXT_TO_SQL_STAGE_ORDER, "understanding");
  if (currentIndex > parallelEnd) {
    return stageVisuals(TEXT_TO_SQL_STAGE_ORDER, latest);
  }
  return TEXT_TO_SQL_STAGE_ORDER.map((stage) => ({
    stage,
    visual: parallelVisual(stage, report, currentIndex >= 0),
  }));
}

function parallelStage(stage: string): ParallelStage | null {
  if (stage === "checking" || stage === "understanding") return stage;
  return null;
}

function parallelVisual(stage: string, report: TextToSqlStageReport, pairOpen: boolean): StageVisual {
  const parallel = parallelStage(stage);
  if (parallel === null || !pairOpen) return "pending";
  const status = report.pair[parallel];
  if (status === "done") return "complete";
  if (status === "failed") return "failed";
  const other: ParallelStage = parallel === "checking" ? "understanding" : "checking";
  if (report.pair[other] === "failed" && status === undefined) return "pending";
  return "current";
}

function visualAt(index: number, currentIndex: number, snapshot: StageSnapshot | null): StageVisual {
  if (snapshot === null || currentIndex < 0) return "pending";
  if (index < currentIndex) return "complete";
  if (index > currentIndex) return "pending";
  switch (snapshot.status) {
    case "running":
      return "current";
    case "done":
      return "complete";
    case "failed":
      return "failed";
    default: {
      const _never: never = snapshot.status;
      return _never;
    }
  }
}
