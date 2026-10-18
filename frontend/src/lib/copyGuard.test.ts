import { afterEach, describe, expect, it, vi } from "vitest";

import { installQuizCopyGuard } from "./copyGuard";

const CLIPBOARD = ["copy", "cut", "paste", "contextmenu"] as const;

describe("installQuizCopyGuard", () => {
  afterEach(() => {
    document.body.replaceChildren();
  });

  it("blocks clipboard and selection events inside the quiz root, including inputs", () => {
    const root = document.createElement("section");
    const input = document.createElement("input");
    const prompt = document.createElement("p");
    prompt.textContent = "What is a square?";
    root.append(prompt, input);
    const outside = document.createElement("p");
    outside.textContent = "lesson notes";
    document.body.append(root, outside);
    const onBlocked = vi.fn();
    const uninstall = installQuizCopyGuard(root, onBlocked);

    for (const type of CLIPBOARD) {
      const onPrompt = new Event(type, { bubbles: true, cancelable: true });
      prompt.dispatchEvent(onPrompt);
      expect(onPrompt.defaultPrevented).toBe(true);

      const onInput = new Event(type, { bubbles: true, cancelable: true });
      input.dispatchEvent(onInput);
      expect(onInput.defaultPrevented).toBe(true);

      const onOutside = new Event(type, { bubbles: true, cancelable: true });
      outside.dispatchEvent(onOutside);
      expect(onOutside.defaultPrevented).toBe(false);
    }

    const selectInside = new Event("selectstart", { bubbles: true, cancelable: true });
    input.dispatchEvent(selectInside);
    expect(selectInside.defaultPrevented).toBe(true);

    const selectOutside = new Event("selectstart", { bubbles: true, cancelable: true });
    outside.dispatchEvent(selectOutside);
    expect(selectOutside.defaultPrevented).toBe(false);

    expect(onBlocked).toHaveBeenCalledTimes(CLIPBOARD.length * 2);

    uninstall();
    const after = new Event("paste", { bubbles: true, cancelable: true });
    input.dispatchEvent(after);
    expect(after.defaultPrevented).toBe(false);
  });
});
