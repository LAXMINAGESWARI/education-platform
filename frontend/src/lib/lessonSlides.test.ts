import { describe, expect, it } from "vitest";

import type { LessonMaterial } from "../api/types";
import { mensurationLessonMarkdown } from "./mensurationFixture";
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

  it("merges a short h3 into the same h2 slide", () => {
    const slides = slidesFromMarkdown(
      [
        "## Trapezoids",
        "",
        "A short note.",
        "",
        "### Formula",
        "",
        "$$\\frac{1}{2} \\times (base_1 + base_2) \\times height$$",
        "",
        "### Check",
        "",
        "Subtract the inner shape.",
      ].join("\n"),
      "Mensuration",
    );
    expect(slides).toHaveLength(1);
    expect(slides[0]?.sectionTitle).toBe("Trapezoids");
    expect(slides[0]?.content).toContain("### Formula");
    expect(slides[0]?.content).toContain("### Check");
    expect(slides[0]?.content).toContain("\\frac{1}{2}");
  });

  it("splits h2 and h3 sections without cutting a table or a multi-line formula", () => {
    const formula = [
      "$$\\text{Required area} = (l + 2w)(b + 2w) - lb",
      "= 2400 + 3200 + (2 \\times 1200) = 8000 \\, \\text{cm}^2.$$",
    ].join("\n");
    const table = ["| Shape | Area |", "| --- | --- |", "| Park | 1200 |"].join("\n");
    const filler = Array.from(
      { length: 30 },
      (_, index) => `Sentence ${index} walks through the outer rectangle in plain language.`,
    ).join("\n\n");
    const markdown = [
      "# Mensuration",
      "",
      "## Area of the path",
      "",
      filler,
      "",
      table,
      "",
      formula,
      "",
      filler,
      "",
      "### Check",
      "",
      "Subtract the park.",
    ].join("\n");
    const slides = slidesFromMarkdown(markdown, "Mensuration");
    expect(slides.every((slide) => slide.sectionTitle === "Area of the path")).toBe(true);
    expect(slides.some((slide) => slide.content.includes("Subtract the park."))).toBe(true);
    const formulaSlides = slides.filter(
      (slide) => slide.content.includes("Required area") || slide.content.includes("8000"),
    );
    expect(formulaSlides).toHaveLength(1);
    expect(formulaSlides[0]?.content).toContain("| Park | 1200 |");
    expect(formulaSlides[0]?.content).toContain("Required area");
    expect(formulaSlides[0]?.content).toContain("8000");
    expect(slides.filter((slide) => slide.content.includes("| Shape | Area |"))).toHaveLength(1);
    expect(formulaSlides[0]?.content.split("$$").length - 1).toBe(2);
  });

  it("packs a full mensuration lesson into a screenful of slides without cutting tables or formulas", () => {
    const markdown = mensurationLessonMarkdown();
    expect(markdown.length).toBeGreaterThan(60_000);
    const slides = slidesFromMarkdown(markdown, "Mensuration");
    expect(slides.length).toBeGreaterThanOrEqual(20);
    expect(slides.length).toBeLessThanOrEqual(35);
    const sections = new Set(slides.map((slide) => slide.sectionTitle));
    expect(sections.size).toBe(12);
    const tables = slides.filter((slide) => slide.content.includes("| Shape | Length |"));
    expect(tables).toHaveLength(12);
    for (const slide of tables) {
      const rows = slide.content
        .split("\n")
        .filter((line) => /^\|/.test(line) && !line.includes("---"));
      expect(rows.length).toBe(7);
    }
    const formulas = slides.filter((slide) => slide.content.includes("$$"));
    expect(formulas).toHaveLength(12);
    for (const slide of formulas) {
      expect(slide.content.split("$$").length - 1).toBe(2);
      expect(slide.content).toContain("\n=");
    }
  });

  it("wraps heading-less markdown as a single slide", () => {
    const slides = slidesFromMarkdown("Just a paragraph.", "Approved Materials");
    expect(slides).toEqual([
      { number: 1, title: "Approved Materials", content: "Just a paragraph." },
    ]);
  });
});
