const HEADING_SELECTOR = "h1, h2, h3, h4, h5, h6";

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
  focusTarget.focus();
  return true;
}
