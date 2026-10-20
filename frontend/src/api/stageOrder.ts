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
