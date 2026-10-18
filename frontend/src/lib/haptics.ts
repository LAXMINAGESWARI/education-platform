import { prefersReducedMotion } from "./motionPrefs";

/**
 * Subtle tap haptics for phones.
 *
 * Android / Chrome use `navigator.vibrate` and only while the document still
 * has transient user activation, so Chrome does not log a vibration
 * intervention warning.
 *
 * iOS Safari (17.4+ / 18+) has no Vibration API. WebKit plays the system
 * haptic when a checkbox that carries the non-standard `switch` attribute is
 * toggled by a click on its label. That click has to run synchronously inside
 * the user's pointer/click dispatch. A second pulse scheduled with a timeout
 * is no longer a user gesture, and Safari does not buzz again, so every
 * pattern is one switch click on iOS.
 *
 * Anywhere else the calls no-op.
 */

export type HapticPattern = "tap" | "success" | "error" | "selection";

export const HAPTIC_PATTERNS: Record<HapticPattern, number | number[]> = {
  tap: 10,
  selection: 10,
  success: [15, 40, 15],
  error: [30, 40, 30],
};

const STORAGE_KEY = "ep.haptics";
const MOVE_THRESHOLD_PX = 10;

const INTERACTIVE_SELECTOR = [
  "button",
  "[role='button']",
  "[role='tab']",
  "[role='option']",
  "[role='menuitem']",
  "[role='radio']",
  "[role='switch']",
  "[role='checkbox']",
  "a.btn",
  "a[class*='btn']",
  "a.rail__link",
  "select",
  "input[type='checkbox']",
  "input[type='radio']",
  "label",
].join(",");

type HapticEngine = "vibrate" | "ios-switch" | "none";

type PointerGesture = {
  id: number;
  x: number;
  y: number;
  pointerType: string;
  moved: boolean;
};

type ClaimedEvent = Event & { hapticClaim?: boolean };

let installed = false;
let removeListeners: (() => void) | null = null;
let switchLabel: HTMLLabelElement | null = null;
let switchInput: HTMLInputElement | null = null;
let activePointer: PointerGesture | null = null;
let armedPattern: HapticPattern | null = null;
let gestureSerial = 0;
let claimedSerial = -1;
let pulsedSerial = -1;

function readStored(): boolean | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    if (value === "on") return true;
    if (value === "off") return false;
    return null;
  } catch {
    return null;
  }
}

function isIosWebKit(): boolean {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent;
  if (!/WebKit/i.test(ua)) return false;
  if (/iPad|iPhone|iPod/.test(ua)) return true;
  return navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1;
}

export function hapticEngine(): HapticEngine {
  if (typeof navigator === "undefined") return "none";
  if (typeof navigator.vibrate === "function") return "vibrate";
  if (isIosWebKit()) return "ios-switch";
  return "none";
}

function isTouchDevice(): boolean {
  if (typeof navigator !== "undefined" && navigator.maxTouchPoints > 0) return true;
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(pointer: coarse)").matches;
}

/** Phones that can actually buzz. Desktop Chrome exposes vibrate and stays hidden. */
export function hapticsSupported(): boolean {
  if (hapticEngine() === "none") return false;
  return isTouchDevice() || hapticEngine() === "ios-switch";
}

/** Persisted choice. Unset follows touch + prefers-reduced-motion. */
export function isHapticsEnabled(): boolean {
  const stored = readStored();
  if (stored !== null) return stored;
  if (prefersReducedMotion()) return false;
  return isTouchDevice();
}

export function setHapticsEnabled(enabled: boolean): void {
  try {
    localStorage.setItem(STORAGE_KEY, enabled ? "on" : "off");
  } catch {
    /* private mode */
  }
}

function userActivationCurrent(): boolean {
  const activation = navigator.userActivation;
  if (!activation) return true;
  return activation.isActive;
}

function patternValue(pattern: HapticPattern): number | number[] {
  switch (pattern) {
    case "tap":
      return HAPTIC_PATTERNS.tap;
    case "selection":
      return HAPTIC_PATTERNS.selection;
    case "success":
      return HAPTIC_PATTERNS.success;
    case "error":
      return HAPTIC_PATTERNS.error;
    default: {
      const neverPattern: never = pattern;
      return neverPattern;
    }
  }
}

function applyHidden(element: HTMLElement): void {
  element.style.position = "fixed";
  element.style.top = "0";
  element.style.left = "0";
  element.style.width = "1px";
  element.style.height = "1px";
  element.style.margin = "0";
  element.style.padding = "0";
  element.style.border = "0";
  element.style.opacity = "0";
  element.style.overflow = "hidden";
  element.style.pointerEvents = "none";
  element.style.outline = "none";
  element.style.clipPath = "inset(50%)";
}

/**
 * One visually hidden switch, created on the first iOS pulse.
 * `display: none` / `visibility: hidden` skip the haptic, so this stays
 * rendered at 1px, inert to hit-testing, and out of the accessibility tree.
 */
function ensureIosSwitch(): HTMLLabelElement {
  if (switchLabel?.isConnected && switchInput) return switchLabel;

  const label = document.createElement("label");
  label.dataset.hapticSwitch = "";
  label.dataset.haptics = "off";
  label.setAttribute("aria-hidden", "true");
  label.tabIndex = -1;
  applyHidden(label);

  const input = document.createElement("input");
  input.type = "checkbox";
  input.setAttribute("switch", "");
  input.tabIndex = -1;
  input.setAttribute("aria-hidden", "true");
  input.dataset.haptics = "off";
  applyHidden(input);
  input.addEventListener("focus", () => {
    input.blur();
  });
  label.addEventListener("click", (event) => {
    event.stopPropagation();
  });

  label.appendChild(input);
  document.body.appendChild(label);
  switchLabel = label;
  switchInput = input;
  return label;
}

function pulseIosSwitch(): void {
  const label = ensureIosSwitch();
  const input = switchInput;
  const previous = document.activeElement;
  const scrollX = window.scrollX;
  const scrollY = window.scrollY;
  label.click();
  if (input && document.activeElement === input) input.blur();
  if (
    previous instanceof HTMLElement &&
    previous !== input &&
    previous.isConnected &&
    document.activeElement !== previous
  ) {
    previous.focus({ preventScroll: true });
  }
  if (window.scrollX !== scrollX || window.scrollY !== scrollY) {
    window.scrollTo(scrollX, scrollY);
  }
}

function play(pattern: HapticPattern, fromListener: boolean): void {
  if (!isHapticsEnabled()) return;
  if (!userActivationCurrent()) return;
  // A label click also synthesizes a click on its control. That second event
  // shares the gesture serial; a later real tap gets a new one.
  if (fromListener) {
    if (pulsedSerial === gestureSerial) return;
    pulsedSerial = gestureSerial;
  }

  const engine = hapticEngine();
  if (engine === "vibrate") {
    const value = patternValue(pattern);
    try {
      navigator.vibrate(value);
    } catch {
      /* blocked */
    }
    return;
  }
  if (engine === "ios-switch") {
    pulseIosSwitch();
  }
}

function isDisabled(element: Element): boolean {
  if (element.matches(":disabled")) return true;
  return element.getAttribute("aria-disabled") === "true";
}

function labeledControl(label: HTMLLabelElement): Element | null {
  if (label.control) return label.control;
  return label.querySelector("button, select, input[type='checkbox'], input[type='radio']");
}

function labelActivatesHaptic(label: HTMLLabelElement): boolean {
  const control = labeledControl(label);
  if (!control || control.closest("[data-haptics='off']")) return false;
  if (control instanceof HTMLInputElement) {
    return control.type === "checkbox" || control.type === "radio";
  }
  return control instanceof HTMLButtonElement || control instanceof HTMLSelectElement;
}

function resolveInteractive(target: EventTarget | null): Element | null {
  const element =
    target instanceof Element ? target : target instanceof Node ? target.parentElement : null;
  if (!element) return null;
  const match = element.closest(INTERACTIVE_SELECTOR);
  if (!match || match.closest("[data-haptics='off']")) return null;
  if (match instanceof HTMLLabelElement && !labelActivatesHaptic(match)) return null;
  if (isDisabled(match)) return null;
  return match;
}

function isSelectionTarget(element: Element): boolean {
  if (element.matches("input[type='checkbox'], [role='switch'], [role='checkbox']")) return true;
  if (element instanceof HTMLLabelElement) {
    const control = labeledControl(element);
    if (control instanceof HTMLInputElement && control.type === "checkbox") return true;
    if (control?.getAttribute("role") === "switch" || control?.getAttribute("role") === "checkbox") {
      return true;
    }
  }
  return false;
}

function markClaimed(event?: Event): void {
  claimedSerial = gestureSerial;
  if (event) (event as ClaimedEvent).hapticClaim = true;
}

/**
 * Suppress the delegated tap for the gesture already in progress.
 * Call synchronously from the control's click / pointer handler, before any await.
 */
export function claimHaptic(event?: Event): void {
  markClaimed(event);
}

export function triggerHaptic(pattern: HapticPattern, event?: Event): void {
  // A success/error call during this gesture replaces the tap. A later call
  // (after the click listener has cleared the arm) must not swallow the next tap.
  if ((pattern === "success" || pattern === "error") && armedPattern) markClaimed(event);
  play(pattern, false);
}

function rememberPointer(event: PointerEvent): void {
  activePointer = {
    id: event.pointerId,
    x: event.clientX,
    y: event.clientY,
    pointerType: event.pointerType,
    moved: false,
  };
}

function onPointerDown(event: PointerEvent): void {
  rememberPointer(event);
}

function onPointerMove(event: PointerEvent): void {
  if (!activePointer || event.pointerId !== activePointer.id) return;
  const distance = Math.hypot(event.clientX - activePointer.x, event.clientY - activePointer.y);
  if (distance > MOVE_THRESHOLD_PX) activePointer.moved = true;
}

function onPointerUp(event: PointerEvent): void {
  const pointer = activePointer;
  activePointer = null;
  gestureSerial += 1;
  armedPattern = null;
  if (!pointer || pointer.id !== event.pointerId || pointer.moved) return;
  if (event.pointerType === "mouse" || pointer.pointerType === "mouse") return;
  if (event.pointerType !== "touch" && event.pointerType !== "pen") return;
  const distance = Math.hypot(event.clientX - pointer.x, event.clientY - pointer.y);
  if (distance > MOVE_THRESHOLD_PX) return;
  const target = resolveInteractive(event.target);
  if (!target) return;
  armedPattern = isSelectionTarget(target) ? "selection" : "tap";
}

function onPointerCancel(event: PointerEvent): void {
  if (activePointer?.id === event.pointerId) activePointer = null;
  armedPattern = null;
}

/**
 * Bubble, not capture: the control's click handler runs first and can claim
 * success/error for this same gesture. The pulse itself is still synchronous
 * inside click dispatch, which iOS needs for the switch trick.
 * pointerup above is capture so a later stopPropagation cannot hide the pointer type.
 */
function onClick(event: MouseEvent): void {
  const pattern = armedPattern;
  armedPattern = null;
  if (!pattern) return;
  const claimed = (event as ClaimedEvent).hapticClaim === true || claimedSerial === gestureSerial;
  if (claimed) return;
  const target = resolveInteractive(event.target);
  if (!target) return;
  play(isSelectionTarget(target) ? "selection" : "tap", true);
}

export function installHapticFeedback(): () => void {
  if (installed) return uninstallHapticFeedback;
  document.addEventListener("pointerdown", onPointerDown, true);
  document.addEventListener("pointermove", onPointerMove, true);
  document.addEventListener("pointerup", onPointerUp, true);
  document.addEventListener("pointercancel", onPointerCancel, true);
  document.addEventListener("click", onClick, false);
  installed = true;
  removeListeners = () => {
    document.removeEventListener("pointerdown", onPointerDown, true);
    document.removeEventListener("pointermove", onPointerMove, true);
    document.removeEventListener("pointerup", onPointerUp, true);
    document.removeEventListener("pointercancel", onPointerCancel, true);
    document.removeEventListener("click", onClick, false);
  };
  return uninstallHapticFeedback;
}

export function uninstallHapticFeedback(): void {
  removeListeners?.();
  removeListeners = null;
  switchLabel?.remove();
  switchLabel = null;
  switchInput = null;
  installed = false;
  activePointer = null;
  armedPattern = null;
  gestureSerial = 0;
  claimedSerial = -1;
  pulsedSerial = -1;
}
