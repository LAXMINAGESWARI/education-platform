const HEADING_SELECTOR = "h1, h2, h3, h4, h5, h6";

/** Present while a hash heading is focused from script. `:focus-visible` does not match that focus in every browser. */
export const HASH_FOCUS_CLASS = "hash-focus-ring";

/** Scroll a hash target into view and move focus to its heading once it exists. */
export function revealElementById(id: string): boolean {
  const target = document.getElementById(id);
  if (!(target instanceof HTMLElement)) return false;

  if (typeof target.scrollIntoView === "function") {
    target.scrollIntoView({ block: "start" });
  }

  const heading = target.matches(HEADING_SELECTOR)
    ? target
    : target.querySelector(HEADING_SELECTOR);
  const focusTarget = heading instanceof HTMLElement ? heading : target;
  if (focusTarget.tabIndex < 0) {
    focusTarget.tabIndex = -1;
  }
  focusTarget.classList.add(HASH_FOCUS_CLASS);
  focusTarget.addEventListener(
    "blur",
    () => {
      focusTarget.classList.remove(HASH_FOCUS_CLASS);
    },
    { once: true },
  );
  // Default focus scrolling can pin a section at the end of the viewport.
  focusTarget.focus({ preventScroll: true });
  return true;
}

function scheduleFrame(run: () => void): () => void {
  if (typeof requestAnimationFrame === "function") {
    const id = requestAnimationFrame(() => run());
    return () => cancelAnimationFrame(id);
  }
  const id = window.setTimeout(run, 0);
  return () => window.clearTimeout(id);
}

/** Scroll on a later frame so the rendered target has its final position. */
export function revealElementByIdWhenSettled(id: string): () => void {
  let cancelled = false;
  let cancelInner = () => {};
  const cancelOuter = scheduleFrame(() => {
    cancelInner = scheduleFrame(() => {
      if (!cancelled) revealElementById(id);
    });
  });
  return () => {
    cancelled = true;
    cancelOuter();
    cancelInner();
  };
}
