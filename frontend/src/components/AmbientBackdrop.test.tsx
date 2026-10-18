import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AmbientBackdrop } from "./AmbientBackdrop";

type MediaQueryListLike = {
  matches: boolean;
  media: string;
  addEventListener: ReturnType<typeof vi.fn>;
  removeEventListener: ReturnType<typeof vi.fn>;
  addListener?: ReturnType<typeof vi.fn>;
  removeListener?: ReturnType<typeof vi.fn>;
};

function mockMatchMedia(map: Record<string, boolean>) {
  window.matchMedia = vi.fn((query: string): MediaQueryListLike => {
    const matches = Boolean(map[query]);
    return {
      matches,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
    };
  }) as unknown as typeof window.matchMedia;
}

function renderBackdrop(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AmbientBackdrop />
    </MemoryRouter>,
  );
}

describe("AmbientBackdrop", () => {
  beforeEach(() => {
    mockMatchMedia({
      "(prefers-reduced-motion: reduce)": false,
      "(pointer: coarse)": false,
    });
    HTMLCanvasElement.prototype.getContext = vi.fn(() => {
      return {
        setTransform: vi.fn(),
        clearRect: vi.fn(),
        beginPath: vi.fn(),
        moveTo: vi.fn(),
        lineTo: vi.fn(),
        stroke: vi.fn(),
        arc: vi.fn(),
        fill: vi.fn(),
        fillText: vi.fn(),
        fillRect: vi.fn(),
        strokeRect: vi.fn(),
        measureText: vi.fn(() => ({ width: 8 })),
        setLineDash: vi.fn(),
        quadraticCurveTo: vi.fn(),
        save: vi.fn(),
        restore: vi.fn(),
        strokeStyle: "",
        fillStyle: "",
        lineWidth: 1,
        lineCap: "round",
        lineJoin: "round",
        globalAlpha: 1,
        font: "",
        textAlign: "left",
        textBaseline: "alphabetic",
      } as unknown as CanvasRenderingContext2D;
    }) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders doodles on the subjects home page", () => {
    renderBackdrop("/");
    const root = screen.getByTestId("ambient-backdrop");
    expect(root).toHaveAttribute("data-static", "false");
    expect(root).toHaveAttribute("data-doodles", "true");
    expect(root.querySelector("canvas")).not.toBeNull();
  });

  it("keeps the grid without doodles on a subject hub", () => {
    renderBackdrop("/subjects/math-1");
    const root = screen.getByTestId("ambient-backdrop");
    expect(root).toHaveAttribute("data-doodles", "false");
    expect(root.querySelector("canvas")).not.toBeNull();
  });

  it("keeps the grid without doodles on a lesson page", () => {
    renderBackdrop("/subjects/math-1/subtopics/st-1/lesson");
    const root = screen.getByTestId("ambient-backdrop");
    expect(root).toHaveAttribute("data-doodles", "false");
    expect(root.querySelector("canvas")).not.toBeNull();
  });

  it("keeps the grid without doodles on lesson slides and quiz history", () => {
    const { unmount } = renderBackdrop("/subjects/math-1/subtopics/st-1/lesson/slides");
    expect(screen.getByTestId("ambient-backdrop")).toHaveAttribute("data-doodles", "false");
    expect(screen.getByTestId("ambient-backdrop").querySelector("canvas")).not.toBeNull();
    unmount();

    renderBackdrop("/subjects/math-1/subtopics/st-1/lesson/history");
    expect(screen.getByTestId("ambient-backdrop")).toHaveAttribute("data-doodles", "false");
    expect(screen.getByTestId("ambient-backdrop").querySelector("canvas")).not.toBeNull();
  });

  it("keeps the grid without doodles on login and feedback", () => {
    const { unmount } = renderBackdrop("/login");
    expect(screen.getByTestId("ambient-backdrop")).toHaveAttribute("data-doodles", "false");
    expect(screen.getByTestId("ambient-backdrop").querySelector("canvas")).not.toBeNull();
    unmount();

    renderBackdrop("/feedback");
    expect(screen.getByTestId("ambient-backdrop")).toHaveAttribute("data-doodles", "false");
  });

  it("keeps the grid without doodles on a subject feedback page", () => {
    renderBackdrop("/subjects/math-1/feedback");
    const root = screen.getByTestId("ambient-backdrop");
    expect(root).toHaveAttribute("data-doodles", "false");
    expect(root.querySelector("canvas")).not.toBeNull();
  });

  it("hides on admin and other non-student routes", () => {
    renderBackdrop("/admin/materials");
    expect(screen.queryByTestId("ambient-backdrop")).toBeNull();
  });

  it("uses static CSS fallback when reduced motion is preferred", () => {
    mockMatchMedia({
      "(prefers-reduced-motion: reduce)": true,
      "(pointer: coarse)": false,
    });
    renderBackdrop("/");
    const root = screen.getByTestId("ambient-backdrop");
    expect(root).toHaveAttribute("data-static", "true");
    expect(root.className).toContain("ambient-backdrop--static");
    expect(root.querySelector("canvas")).toBeNull();
  });

  it("uses static CSS fallback on coarse pointer", () => {
    mockMatchMedia({
      "(prefers-reduced-motion: reduce)": false,
      "(pointer: coarse)": true,
    });
    renderBackdrop("/");
    const root = screen.getByTestId("ambient-backdrop");
    expect(root).toHaveAttribute("data-static", "true");
    expect(root.querySelector("canvas")).toBeNull();
  });
});
