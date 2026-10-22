import { useEffect, useRef, useState } from "react";
import { matchPath, useLocation } from "react-router-dom";

import { createDoodleEngine } from "../lib/doodleEngine";
import { hasCoarsePointer, prefersReducedMotion } from "../lib/motionPrefs";
import { canStartSketch, createUserSketch } from "../lib/userSketch";

const GRID_SIZE = 40;
const INFLUENCE_RADIUS = 120;
/** Sage accent #3f6f5a */
const ACCENT_RGB = "63, 111, 90";

function drawReactiveGrid(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  mouseX: number,
  mouseY: number,
): void {
  ctx.clearRect(0, 0, width, height);

  ctx.beginPath();
  ctx.strokeStyle = `rgba(${ACCENT_RGB}, 0.12)`;
  ctx.lineWidth = 1;
  for (let x = 0; x <= width; x += GRID_SIZE) {
    ctx.moveTo(x + 0.5, 0);
    ctx.lineTo(x + 0.5, height);
  }
  for (let y = 0; y <= height; y += GRID_SIZE) {
    ctx.moveTo(0, y + 0.5);
    ctx.lineTo(width + 0.5, y + 0.5);
  }
  ctx.stroke();

  for (let x = 0; x <= width; x += GRID_SIZE) {
    for (let y = 0; y <= height; y += GRID_SIZE) {
      const dx = mouseX - x;
      const dy = mouseY - y;
      const distance = Math.hypot(dx, dy);

      if (distance < INFLUENCE_RADIUS) {
        const proximity = 1 - distance / INFLUENCE_RADIUS;
        const opacity = 0.12 + proximity * 0.45;
        const dotSize = 1.5 + proximity * 2.5;
        ctx.beginPath();
        ctx.fillStyle = `rgba(${ACCENT_RGB}, ${opacity})`;
        ctx.arc(x, y, dotSize, 0, Math.PI * 2);
        ctx.fill();
      } else {
        ctx.beginPath();
        ctx.fillStyle = `rgba(${ACCENT_RGB}, 0.1)`;
        ctx.arc(x, y, 1.5, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }
}

/** Routes that show the sage grid. Doodles stay on the subjects home page only. */
export function isAmbientBackdropPath(pathname: string): boolean {
  if (pathname === "/" || pathname === "/login" || pathname === "/feedback") return true;
  if (pathname === "/teacher" || pathname.startsWith("/teacher/")) return true;
  if (pathname === "/admin" || pathname.startsWith("/admin/")) return true;
  if (matchPath({ path: "/subjects/:subjectId", end: true }, pathname)) return true;
  if (matchPath({ path: "/subjects/:subjectId/feedback", end: true }, pathname)) return true;
  if (
    matchPath(
      { path: "/subjects/:subjectId/feedback/topics/:topicId", end: true },
      pathname,
    )
  ) {
    return true;
  }
  if (
    matchPath(
      { path: "/subjects/:subjectId/subtopics/:subtopicId/lesson", end: false },
      pathname,
    )
  ) {
    return true;
  }
  if (matchPath({ path: "/quizzes/:quizId", end: true }, pathname)) return true;
  if (matchPath({ path: "/attempts/:attemptId", end: true }, pathname)) return true;
  return false;
}

/** Doodle choreography only on the subjects home list. */
export function isSubjectsHomePath(pathname: string): boolean {
  return pathname === "/";
}

/**
 * One fixed full-viewport sage grid, mounted above the app in `main.tsx`.
 * Pointer moves only update coordinates; the canvas redraws on the animation
 * frame, so the page tree does not re-render. Portfolio doodles and sketch
 * strokes run only on the subjects home page. Login, teacher, admin, feedback,
 * subject, lesson, quiz, and result pages keep the grid. Reduced motion and
 * coarse pointers use the static CSS grid.
 */
export function AmbientBackdrop() {
  const { pathname } = useLocation();
  const active = isAmbientBackdropPath(pathname);
  const doodlesEnabled = isSubjectsHomePath(pathname);

  const canvasRef = useRef<HTMLCanvasElement>(null);

  const [staticOnly, setStaticOnly] = useState(() => {
    if (typeof window === "undefined") return true;
    return prefersReducedMotion() || hasCoarsePointer();
  });

  useEffect(() => {
    const mqReduce = window.matchMedia("(prefers-reduced-motion: reduce)");
    const mqCoarse = window.matchMedia("(pointer: coarse)");

    const syncMode = () => {
      setStaticOnly(mqReduce.matches || mqCoarse.matches);
    };
    syncMode();

    mqReduce.addEventListener("change", syncMode);
    mqCoarse.addEventListener("change", syncMode);
    return () => {
      mqReduce.removeEventListener("change", syncMode);
      mqCoarse.removeEventListener("change", syncMode);
    };
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !active || staticOnly) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const doodle = doodlesEnabled ? createDoodleEngine() : null;
    const sketch = doodlesEnabled ? createUserSketch() : null;
    let width = 0;
    let height = 0;
    let mouseX = -1000;
    let mouseY = -1000;
    let targetX = -1000;
    let targetY = -1000;
    let rafId = 0;
    let running = true;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      doodle?.resize(width, height);
      sketch?.resize(width, height);
    };

    const onPointerMove = (event: PointerEvent) => {
      targetX = event.clientX;
      targetY = event.clientY;
      if (sketch?.isDrawing()) {
        sketch.extend(event.clientX, event.clientY);
      }
    };

    const onPointerDown = (event: PointerEvent) => {
      if (event.button !== 0 || !sketch) return;
      if (!canStartSketch(event.target)) return;
      sketch.begin(event.clientX, event.clientY);
    };

    const onPointerUp = (event: PointerEvent) => {
      if (!sketch?.isDrawing()) return;
      sketch.end(event.clientX, event.clientY);
    };

    const onPointerLeave = () => {
      targetX = -1000;
      targetY = -1000;
    };

    const tick = () => {
      if (!running) return;
      if (!document.hidden) {
        mouseX += (targetX - mouseX) * 0.35;
        mouseY += (targetY - mouseY) * 0.35;
        drawReactiveGrid(ctx, width, height, mouseX, mouseY);
        doodle?.tick(ctx, Date.now());
        sketch?.tick(ctx, Date.now());
      }
      rafId = window.requestAnimationFrame(tick);
    };

    resize();
    window.addEventListener("resize", resize);
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("pointermove", onPointerMove, { passive: true });
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
    document.documentElement.addEventListener("pointerleave", onPointerLeave);
    rafId = window.requestAnimationFrame(tick);

    return () => {
      running = false;
      window.cancelAnimationFrame(rafId);
      doodle?.dispose();
      sketch?.dispose();
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerUp);
      document.documentElement.removeEventListener("pointerleave", onPointerLeave);
    };
  }, [active, staticOnly, doodlesEnabled]);

  if (!active) return null;

  return (
    <div
      className={`ambient-backdrop ${staticOnly ? "ambient-backdrop--static" : ""}`}
      aria-hidden="true"
      data-testid="ambient-backdrop"
      data-static={staticOnly ? "true" : "false"}
      data-doodles={doodlesEnabled ? "true" : "false"}
    >
      {!staticOnly && <canvas ref={canvasRef} className="ambient-backdrop__canvas" />}
    </div>
  );
}
