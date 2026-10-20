import { describe, expect, it, vi } from "vitest";

import { HASH_FOCUS_CLASS, revealElementById } from "./hashFocus";

describe("revealElementById", () => {
  it("scrolls a section and focuses the heading inside it", () => {
    document.body.innerHTML =
      '<section id="feedback-due"><h2>Due for review</h2><p>One subtopic</p></section>';
    const section = document.getElementById("feedback-due");
    const heading = section?.querySelector("h2");
    if (!(section instanceof HTMLElement) || !(heading instanceof HTMLElement)) {
      throw new Error("fixture missing");
    }
    section.scrollIntoView = vi.fn();

    expect(revealElementById("feedback-due")).toBe(true);
    expect(section.scrollIntoView).toHaveBeenCalledWith({ block: "start" });
    expect(heading).toHaveAttribute("tabindex", "-1");
    expect(heading).toHaveFocus();
    expect(heading).toHaveClass(HASH_FOCUS_CLASS);
  });

  it("focuses a heading that is the hash target", () => {
    document.body.innerHTML = '<h2 id="needs-attention" tabindex="-1">Needs attention: Algebra</h2>';
    const heading = document.getElementById("needs-attention");
    if (!(heading instanceof HTMLElement)) throw new Error("fixture missing");
    heading.scrollIntoView = vi.fn();

    expect(revealElementById("needs-attention")).toBe(true);
    expect(heading.scrollIntoView).toHaveBeenCalledWith({ block: "start" });
    expect(heading).toHaveFocus();
    expect(heading).toHaveClass(HASH_FOCUS_CLASS);
    heading.blur();
    expect(heading).not.toHaveClass(HASH_FOCUS_CLASS);
  });

  it("does nothing when the target is not in the document yet", () => {
    document.body.innerHTML = "";
    expect(revealElementById("needs-attention")).toBe(false);
  });
});
