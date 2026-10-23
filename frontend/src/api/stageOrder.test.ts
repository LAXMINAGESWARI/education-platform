import { describe, expect, it } from "vitest";

import {
  TEXT_TO_SQL_STAGE_ORDER,
  UPLOAD_STAGE_ORDER,
  emptyTextToSqlStageReport,
  noteProgressSeq,
  noteTextToSqlStage,
  resetProgressCursor,
  stageVisuals,
  terminalTextToSqlStageReport,
  textToSqlStageVisuals,
  type StageSnapshot,
  type StageVisual,
} from "./stageOrder";

function visuals(order: readonly string[], snapshot: StageSnapshot | null): StageVisual[] {
  return stageVisuals(order, snapshot).map((item) => item.visual);
}

describe("stage orders", () => {
  it("lists upload work stages and keeps failed out of that list", () => {
    expect(UPLOAD_STAGE_ORDER).toEqual([
      "received",
      "parsing",
      "chunking",
      "embedding",
      "extracting_topics",
      "done",
    ]);
  });

  it("lists text-to-SQL stages in pipeline order", () => {
    expect(TEXT_TO_SQL_STAGE_ORDER).toEqual([
      "checking",
      "understanding",
      "writing_query",
      "validating",
      "running",
      "summarising",
    ]);
  });
});

describe("stageVisuals", () => {
  it("marks every earlier upload stage done when parsing is skipped for embedding", () => {
    expect(visuals(UPLOAD_STAGE_ORDER, { stage: "embedding", status: "running" })).toEqual([
      "complete",
      "complete",
      "complete",
      "current",
      "pending",
      "pending",
    ]);
  });

  it("marks earlier text-to-SQL stages done when the stream skips to running", () => {
    expect(visuals(TEXT_TO_SQL_STAGE_ORDER, { stage: "running", status: "running" })).toEqual([
      "complete",
      "complete",
      "complete",
      "complete",
      "current",
      "pending",
    ]);
  });

  it("does not mark done complete when the upload stage is the failed terminal", () => {
    expect(visuals(UPLOAD_STAGE_ORDER, { stage: "failed", status: "failed" })).toEqual([
      "pending",
      "pending",
      "pending",
      "pending",
      "pending",
      "pending",
    ]);
  });

  it("fails the current in-order stage and leaves later stages pending", () => {
    expect(visuals(TEXT_TO_SQL_STAGE_ORDER, { stage: "checking", status: "failed" })).toEqual([
      "failed",
      "pending",
      "pending",
      "pending",
      "pending",
      "pending",
    ]);
  });
});

describe("textToSqlStageVisuals", () => {
  function visualsFor(events: StageSnapshot[]): StageVisual[] {
    const report = events.reduce(
      (current, event) => noteTextToSqlStage(current, event),
      emptyTextToSqlStageReport(),
    );
    return textToSqlStageVisuals(report).map((item) => item.visual);
  }

  it("fills every stage from a terminal-only final replay", () => {
    const visuals = textToSqlStageVisuals(terminalTextToSqlStageReport("done")).map(
      (item) => item.visual,
    );
    expect(visuals).toEqual(["complete", "complete", "complete", "complete", "complete", "complete"]);
  });

  it("marks checking failed when a terminal-only refusal has no progress", () => {
    expect(
      textToSqlStageVisuals(terminalTextToSqlStageReport("failed")).map((item) => item.visual),
    ).toEqual(["failed", "pending", "pending", "pending", "pending", "pending"]);
  });

  it("keeps checking and understanding in progress together", () => {
    expect(
      visualsFor([
        { stage: "checking", status: "running" },
        { stage: "understanding", status: "running" },
      ]),
    ).toEqual(["current", "current", "pending", "pending", "pending", "pending"]);
  });

  it("marks only the stage that reported done", () => {
    expect(
      visualsFor([
        { stage: "checking", status: "done" },
        { stage: "understanding", status: "running" },
      ]),
    ).toEqual(["complete", "current", "pending", "pending", "pending", "pending"]);
  });

  it("leaves understanding pending when checking fails", () => {
    expect(visualsFor([{ stage: "checking", status: "failed" }])).toEqual([
      "failed",
      "pending",
      "pending",
      "pending",
      "pending",
      "pending",
    ]);
  });

  it("marks both early stages done once a later stage arrives", () => {
    expect(visualsFor([{ stage: "running", status: "running" }])).toEqual([
      "complete",
      "complete",
      "complete",
      "complete",
      "current",
      "pending",
    ]);
  });
});

describe("noteProgressSeq", () => {
  it("accepts a seq gap and ignores a lower seq", () => {
    const first = noteProgressSeq(resetProgressCursor(), 1);
    expect(first).toEqual({ cursor: { seq: 1 }, accept: true });

    const gap = noteProgressSeq(first.cursor, 4);
    expect(gap).toEqual({ cursor: { seq: 4 }, accept: true });

    const stale = noteProgressSeq(gap.cursor, 2);
    expect(stale).toEqual({ cursor: { seq: 4 }, accept: false });
  });

  it("accepts a mid-stream snapshot after a reconnect resets the cursor", () => {
    const seen = noteProgressSeq(noteProgressSeq(resetProgressCursor(), 1).cursor, 5);
    expect(noteProgressSeq(seen.cursor, 2)).toEqual({ cursor: { seq: 5 }, accept: false });

    const snapshot: StageSnapshot = { stage: "embedding", status: "running" };
    const restarted = noteProgressSeq(resetProgressCursor(), 2);
    expect(restarted).toEqual({ cursor: { seq: 2 }, accept: true });
    expect(visuals(UPLOAD_STAGE_ORDER, snapshot)).toEqual([
      "complete",
      "complete",
      "complete",
      "current",
      "pending",
      "pending",
    ]);
  });
});
