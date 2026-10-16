import { describe, expect, it } from "vitest";

import type { LessonMaterial } from "../api/types";
import { findSummarySlide, slidesForLessonView, slidesFromMarkdown } from "./lessonSlides";

function lesson(over: Partial<LessonMaterial> = {}): LessonMaterial {
  return {
    id: "topic-1",
    title: "Approved Materials",
    markdown: "",
    slides: [],
    source_material_version_id: "ver-1",
    quiz_unlocked: true,
    quiz_id: "quiz-1",
    progress: null,
    ...over,
  };
}

describe("lessonSlides", () => {
  it("prefers a lesson summary slide, then the last slide", () => {
    expect(
      findSummarySlide([
        { number: 1, title: "Intro", content: "A" },
        { number: 2, title: "Lesson Summary", content: "B" },
      ])?.content,
    ).toBe("B");
    expect(findSummarySlide([{ number: 1, title: "Only", content: "A" }])?.title).toBe("Only");
    expect(findSummarySlide([])).toBeNull();
  });

  it("uses API slides when present", () => {
    const slides = slidesForLessonView(
      lesson({
        markdown: "## Ignored\n\nNope.",
        slides: [{ number: 1, title: "From API", content: "Hello." }],
      }),
    );
    expect(slides).toEqual([{ number: 1, title: "From API", content: "Hello." }]);
  });

  it("parses canonical slide headings from markdown", () => {
    const slides = slidesFromMarkdown(
      "# Title\n\n## Slide 1 — Welcome\n\nIntro.\n\n## Slide 2 — Lesson Summary\n\nRecap.",
      "Fallback",
    );
    expect(slides).toEqual([
      { number: 1, title: "Welcome", content: "Intro." },
      { number: 2, title: "Lesson Summary", content: "Recap." },
    ]);
  });

  it("splits generated # section titles into slides when there is more than one", () => {
    const slides = slidesFromMarkdown(
      "# Fractions\n\nA part of a whole.\n\n# Recap\n\nTwo halves make one.",
      "Approved Materials",
    );
    expect(slides.map((slide) => slide.title)).toEqual(["Fractions", "Recap"]);
    expect(slides[0]?.content).toContain("A part of a whole.");
  });

  it("splits generated ## section headings into slides", () => {
    const slides = slidesForLessonView(
      lesson({
        markdown: "## Fractions\n\nA part of a whole.\n\n## Recap\n\nTwo halves make one.",
      }),
    );
    expect(slides.map((slide) => slide.title)).toEqual(["Fractions", "Recap"]);
    expect(slides[0]?.content).toContain("A part of a whole.");
  });

  it("keeps body text after a single generated h1", () => {
    const slides = slidesFromMarkdown("# Fractions\n\nTwo halves make one.", "Approved Materials");
    expect(slides).toEqual([
      { number: 1, title: "Fractions", content: "Two halves make one." },
    ]);
  });

  it("splits a JSON lesson section and hides the grade-voice line", () => {
    const markdown = [
      "# Lesson",
      "",
      "Write for Grade 8 students studying hello. Use plain language.",
      "",
      "## Introduction",
      "Already taught.",
      "",
      '{"lesson_markdown": "## Solving\\n\\nBoth sides stay balanced."}',
    ].join("\n");
    const slides = slidesFromMarkdown(markdown, "Lesson");
    expect(slides.map((slide) => slide.title)).toEqual(["Introduction", "Solving"]);
    expect(slides[0]?.content).not.toContain("hello");
    expect(slides[1]?.content).toContain("balanced");
    expect(slides[1]?.content).not.toContain("lesson_markdown");
  });

  it("unwraps a wrapping markdown fence before splitting slides", () => {
    const slides = slidesFromMarkdown(
      ["```markdown", "# Fractions", "", "Two halves make one.", "```"].join("\n"),
      "Approved Materials",
    );
    expect(slides).toEqual([
      { number: 1, title: "Fractions", content: "Two halves make one." },
    ]);
  });

  it("wraps heading-less markdown as a single slide", () => {
    const slides = slidesFromMarkdown("Just a paragraph.", "Approved Materials");
    expect(slides).toEqual([
      { number: 1, title: "Approved Materials", content: "Just a paragraph." },
    ]);
  });
});
