import {
  type BeforeInstallPromptEvent,
  INSTALL_DISMISS_KEY,
  isStandaloneDisplay,
  readDismissed,
  rememberDismissed,
} from "./installState";

type Listener = () => void;

let listening = false;
let deferred: BeforeInstallPromptEvent | null = null;
let installed = false;
const listeners = new Set<Listener>();

function emit(): void {
  listeners.forEach((listener) => listener());
}

function onBeforeInstall(event: Event): void {
  event.preventDefault();
  if (readDismissed(INSTALL_DISMISS_KEY) || isStandaloneDisplay()) return;
  deferred = event as BeforeInstallPromptEvent;
  emit();
}

function onInstalled(): void {
  deferred = null;
  installed = true;
  rememberDismissed(INSTALL_DISMISS_KEY);
  emit();
}

/** Register as early as possible so the browser event is not missed. */
export function ensureInstallPromptListener(): void {
  if (listening || typeof window === "undefined") return;
  listening = true;
  window.addEventListener("beforeinstallprompt", onBeforeInstall);
  window.addEventListener("appinstalled", onInstalled);
}

export function subscribeInstallPrompt(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getInstallPrompt(): BeforeInstallPromptEvent | null {
  return deferred;
}

export function wasInstalled(): boolean {
  return installed;
}

export async function promptInstall(): Promise<"accepted" | "dismissed" | "unavailable"> {
  const event = deferred;
  if (!event) return "unavailable";
  await event.prompt();
  const choice = await event.userChoice;
  deferred = null;
  rememberDismissed(INSTALL_DISMISS_KEY);
  emit();
  return choice.outcome;
}

export function dismissInstallPrompt(): void {
  deferred = null;
  rememberDismissed(INSTALL_DISMISS_KEY);
  emit();
}

/** Test helper. Production code does not call this. */
export function resetInstallPromptState(): void {
  deferred = null;
  installed = false;
  if (listening && typeof window !== "undefined") {
    window.removeEventListener("beforeinstallprompt", onBeforeInstall);
    window.removeEventListener("appinstalled", onInstalled);
  }
  listening = false;
  listeners.clear();
}
