import { describe, expect, it, vi } from "vitest";

import {
  drawBackdropChrome,
  getBackdropChrome,
  setBackdropChrome,
  subscribeBackdropChrome,
} from "./backdropChrome";

function mockCtx() {
  return {
    save: vi.fn(),
    restore: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    arcTo: vi.fn(),
    closePath: vi.fn(),
    fill: vi.fn(),
    fillText: vi.fn(),
    measureText: vi.fn(() => ({ width: 64 })),
    fillStyle: "",
    font: "",
    textAlign: "left",
    textBaseline: "alphabetic",
  } as unknown as CanvasRenderingContext2D;
}

describe("backdropChrome", () => {
  it("notifies subscribers when chrome is set and cleared", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeBackdropChrome(listener);
    setBackdropChrome({
      progressPercent: 22,
      progressLabel: "Subject completion · 22%",
      statusLabel: "In progress",
      complete: false,
    });
    expect(getBackdropChrome()?.progressPercent).toBe(22);
    expect(listener).toHaveBeenCalled();
    setBackdropChrome(null);
    expect(getBackdropChrome()).toBeNull();
    unsubscribe();
  });

  it("draws progress into a rect", () => {
    const ctx = mockCtx();
    drawBackdropChrome(
      ctx,
      {
        progressPercent: 22,
        progressLabel: "Subject completion · 22% · 0/2 topics · overall quiz pending",
        statusLabel: "In progress",
        complete: false,
      },
      { left: 100, top: 120, width: 700, height: 52, right: 800, bottom: 172, x: 100, y: 120, toJSON: () => ({}) },
    );
    expect(ctx.fillText).toHaveBeenCalled();
    expect(ctx.fill).toHaveBeenCalled();
  });
});
