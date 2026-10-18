import { createElement } from "react";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  claimHaptic,
  HAPTIC_PATTERNS,
  hapticEngine,
  hapticsSupported,
  installHapticFeedback,
  isHapticsEnabled,
  setHapticsEnabled,
  triggerHaptic,
  uninstallHapticFeedback,
} from "./haptics";

type MediaQueryListLike = {
  matches: boolean;
  media: string;
  addEventListener: ReturnType<typeof vi.fn>;
  removeEventListener: ReturnType<typeof vi.fn>;
};

function mockMatchMedia(map: Record<string, boolean>) {
  window.matchMedia = vi.fn((query: string): MediaQueryListLike => {
    return {
      matches: Boolean(map[query]),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };
  }) as unknown as typeof window.matchMedia;
}

function setNavigator(patch: { vibrate?: unknown; userAgent?: string; maxTouchPoints?: number; userActivation?: unknown }) {
  if ("vibrate" in patch) {
    Object.defineProperty(navigator, "vibrate", {
      configurable: true,
      writable: true,
      value: patch.vibrate,
    });
  }
  if (patch.userAgent != null) {
    Object.defineProperty(navigator, "userAgent", {
      configurable: true,
      value: patch.userAgent,
    });
  }
  if (patch.maxTouchPoints != null) {
    Object.defineProperty(navigator, "maxTouchPoints", {
      configurable: true,
      value: patch.maxTouchPoints,
    });
  }
  if ("userActivation" in patch) {
    Object.defineProperty(navigator, "userActivation", {
      configurable: true,
      value: patch.userActivation,
    });
  }
}

function pointer(type: string, pointerType: string, x: number, y: number) {
  const event = new MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y });
  Object.defineProperties(event, {
    pointerId: { value: 1 },
    pointerType: { value: pointerType },
  });
  return event;
}

function tap(target: Element, pointerType: "touch" | "mouse" | "pen" = "touch", end = { x: 4, y: 4 }) {
  target.dispatchEvent(pointer("pointerdown", pointerType, 2, 2));
  target.dispatchEvent(pointer("pointerup", pointerType, end.x, end.y));
  target.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
}

function drag(target: Element) {
  target.dispatchEvent(pointer("pointerdown", "touch", 0, 0));
  target.dispatchEvent(pointer("pointermove", "touch", 48, 0));
  target.dispatchEvent(pointer("pointerup", "touch", 48, 0));
  target.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
}

describe("haptics", () => {
  const vibrate = vi.fn(() => true);
  let uninstall: () => void;

  beforeEach(() => {
    vibrate.mockClear();
    localStorage.clear();
    mockMatchMedia({
      "(prefers-reduced-motion: reduce)": false,
      "(pointer: coarse)": true,
    });
    setNavigator({
      vibrate,
      maxTouchPoints: 1,
      userAgent: "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile",
      userActivation: undefined,
    });
    uninstall = installHapticFeedback();
    setHapticsEnabled(true);
  });

  afterEach(() => {
    uninstall();
    localStorage.clear();
    document.body.replaceChildren();
  });

  it("vibrates on a touch tap and uses selection for a checkbox", () => {
    const button = document.createElement("button");
    button.textContent = "Next";
    const box = document.createElement("input");
    box.type = "checkbox";
    const link = document.createElement("a");
    link.className = "rail__link";
    link.href = "#home";
    link.textContent = "Home";
    document.body.append(button, box, link);

    tap(button);
    tap(box);
    tap(link);

    expect(vibrate).toHaveBeenNthCalledWith(1, HAPTIC_PATTERNS.tap);
    expect(vibrate).toHaveBeenNthCalledWith(2, HAPTIC_PATTERNS.selection);
    expect(vibrate).toHaveBeenNthCalledWith(3, HAPTIC_PATTERNS.tap);
    expect(hapticEngine()).toBe("vibrate");
    expect(hapticsSupported()).toBe(true);
  });

  it("uses the iOS switch once per call and leaves it out of the accessibility tree", () => {
    setNavigator({
      vibrate: undefined,
      userAgent:
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
    });
    expect(hapticEngine()).toBe("ios-switch");
    expect(hapticsSupported()).toBe(true);

    const button = document.createElement("button");
    button.textContent = "Next";
    document.body.append(button);
    button.focus();

    const clicks = vi.spyOn(HTMLLabelElement.prototype, "click");
    tap(button);
    triggerHaptic("success");

    const label = document.querySelector("label[data-haptic-switch]");
    const input = label?.querySelector("input");
    expect(label).not.toBeNull();
    expect(label).toHaveAttribute("aria-hidden", "true");
    expect(input).toHaveAttribute("tabindex", "-1");
    expect(input).toHaveAttribute("aria-hidden", "true");
    expect(input).toHaveAttribute("switch", "");
    expect((input as HTMLInputElement).form).toBeNull();
    expect(label).toHaveAttribute("data-haptics", "off");
    expect((label as HTMLElement).style.opacity).toBe("0");
    expect((label as HTMLElement).style.position).toBe("fixed");
    expect(clicks).toHaveBeenCalledTimes(2);
    expect(document.activeElement).toBe(button);
    clicks.mockRestore();
  });

  it("prefers vibrate over the iOS switch when both could match", () => {
    setNavigator({
      vibrate,
      userAgent:
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/120.0.0.0 Mobile/15E148",
    });
    const button = document.createElement("button");
    document.body.append(button);
    tap(button);
    expect(vibrate).toHaveBeenCalledWith(HAPTIC_PATTERNS.tap);
    expect(document.querySelector("input[switch]")).toBeNull();
  });

  it("does not pulse when the saved setting is off", () => {
    setHapticsEnabled(false);
    expect(isHapticsEnabled()).toBe(false);
    const button = document.createElement("button");
    document.body.append(button);
    tap(button);
    triggerHaptic("error");
    expect(vibrate).not.toHaveBeenCalled();
  });

  it("defaults off under reduced motion and on for an untouched touch device", () => {
    localStorage.clear();
    mockMatchMedia({
      "(prefers-reduced-motion: reduce)": true,
      "(pointer: coarse)": true,
    });
    expect(isHapticsEnabled()).toBe(false);
    const button = document.createElement("button");
    document.body.append(button);
    tap(button);
    expect(vibrate).not.toHaveBeenCalled();

    mockMatchMedia({
      "(prefers-reduced-motion: reduce)": false,
      "(pointer: coarse)": true,
    });
    expect(isHapticsEnabled()).toBe(true);
    tap(button);
    expect(vibrate).toHaveBeenCalledWith(HAPTIC_PATTERNS.tap);
  });

  it("ignores opted-out controls, text labels, mouse pointers, and drags", () => {
    const opted = document.createElement("button");
    opted.dataset.haptics = "off";
    opted.textContent = "Skip";
    const email = document.createElement("label");
    email.textContent = "Email";
    const field = document.createElement("input");
    field.type = "email";
    email.append(field);
    const plain = document.createElement("button");
    plain.textContent = "Save";
    document.body.append(opted, email, plain);

    tap(opted);
    tap(email);
    tap(plain, "mouse");
    drag(plain);

    expect(vibrate).not.toHaveBeenCalled();
  });

  it("plays success instead of a second tap when the click claims the gesture", () => {
    render(
      createElement(
        "button",
        {
          type: "button",
          onClick: () => {
            claimHaptic();
            triggerHaptic("success");
          },
        },
        "Save",
      ),
    );
    tap(screen.getByRole("button", { name: "Save" }));
    expect(vibrate).toHaveBeenCalledTimes(1);
    expect(vibrate).toHaveBeenCalledWith(HAPTIC_PATTERNS.success);

    const later = document.createElement("button");
    later.textContent = "Later";
    document.body.append(later);
    tap(later);
    expect(vibrate).toHaveBeenCalledTimes(2);
    expect(vibrate).toHaveBeenLastCalledWith(HAPTIC_PATTERNS.tap);
  });

  it("skips vibrate when the user activation has already expired", () => {
    setNavigator({ userActivation: { isActive: false, hasBeenActive: true } });
    triggerHaptic("error");
    expect(vibrate).not.toHaveBeenCalled();

    setNavigator({ userActivation: { isActive: true, hasBeenActive: true } });
    triggerHaptic("error");
    expect(vibrate).toHaveBeenCalledWith(HAPTIC_PATTERNS.error);
  });

  it("hides the control on a desktop pointer even if vibrate exists", () => {
    setNavigator({ maxTouchPoints: 0 });
    mockMatchMedia({
      "(prefers-reduced-motion: reduce)": false,
      "(pointer: coarse)": false,
    });
    expect(hapticsSupported()).toBe(false);
    expect(isHapticsEnabled()).toBe(true);
  });
});

describe("haptics install", () => {
  it("uninstalls cleanly", () => {
    const stop = installHapticFeedback();
    expect(installHapticFeedback()).toBe(stop);
    uninstallHapticFeedback();
    const vibrate = vi.fn();
    setNavigator({ vibrate, maxTouchPoints: 1 });
    setHapticsEnabled(true);
    const button = document.createElement("button");
    document.body.append(button);
    tap(button);
    expect(vibrate).not.toHaveBeenCalled();
  });
});
