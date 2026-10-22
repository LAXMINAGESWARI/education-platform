import { describe, expect, it } from "vitest";

import { parseProgressEnvelope } from "./progressEnvelope";

const envelope = {
  v: 1,
  kind: "text_to_sql",
  subject_id: "2f1c0c2e-7b1a-4e0a-9c3d-1a2b3c4d5e6f",
  seq: 2,
  stage: "validating",
  status: "running",
  percent: null,
  current: null,
  total: null,
  unit: null,
  message: "Checking the query",
  updated_at: "2026-10-20T11:00:00Z",
};

describe("parseProgressEnvelope", () => {
  it("reads the shared progress fields", () => {
    expect(parseProgressEnvelope(JSON.stringify(envelope))).toEqual(envelope);
  });

  it("keeps a numeric percent for streams that report one", () => {
    expect(parseProgressEnvelope(JSON.stringify({ ...envelope, percent: 40, kind: "upload" })).percent).toBe(
      40,
    );
  });

  it("rejects a broken envelope", () => {
    expect(() => parseProgressEnvelope("{")).toThrow(SyntaxError);
    expect(() => parseProgressEnvelope(JSON.stringify({ ...envelope, v: 2 }))).toThrow(SyntaxError);
    expect(() => parseProgressEnvelope(JSON.stringify({ ...envelope, seq: 0 }))).toThrow(SyntaxError);
  });
});
