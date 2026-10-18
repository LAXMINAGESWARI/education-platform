/** Clipboard lock for the in-progress student quiz. Listeners stay on that subtree. */

export const QUIZ_COPY_MESSAGE = "Copy and paste are disabled during quizzes";

const CLIPBOARD_EVENTS = ["copy", "cut", "paste", "contextmenu"] as const;

export function installQuizCopyGuard(
  root: HTMLElement,
  onBlocked?: () => void,
): () => void {
  const blockClipboard = (event: Event) => {
    event.preventDefault();
    onBlocked?.();
  };
  const blockSelection = (event: Event) => {
    event.preventDefault();
  };

  for (const name of CLIPBOARD_EVENTS) {
    root.addEventListener(name, blockClipboard);
  }
  root.addEventListener("selectstart", blockSelection);
  return () => {
    for (const name of CLIPBOARD_EVENTS) {
      root.removeEventListener(name, blockClipboard);
    }
    root.removeEventListener("selectstart", blockSelection);
  };
}
