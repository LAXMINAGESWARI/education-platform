export const INSTALL_BANNER_KEY = "ep.pwa.installBanner";
export const INSTALL_BANNER_SNOOZE_MS = 3 * 24 * 60 * 60 * 1000;
export const INSTALL_BANNER_DISMISS_LIMIT = 3;

export type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
};

export type InstallPlatform = "ios" | "android" | "desktop";

type BannerSnooze = {
  count: number;
  until: number;
};

const EMPTY_SNOOZE: BannerSnooze = { count: 0, until: 0 };

function readBannerSnooze(): BannerSnooze {
  try {
    const raw = localStorage.getItem(INSTALL_BANNER_KEY);
    if (!raw) return EMPTY_SNOOZE;
    const parsed = JSON.parse(raw) as Partial<BannerSnooze>;
    const count = typeof parsed.count === "number" && parsed.count > 0 ? Math.floor(parsed.count) : 0;
    const until = typeof parsed.until === "number" && Number.isFinite(parsed.until) ? parsed.until : 0;
    return { count, until };
  } catch {
    return EMPTY_SNOOZE;
  }
}

function writeBannerSnooze(state: BannerSnooze): void {
  try {
    localStorage.setItem(INSTALL_BANNER_KEY, JSON.stringify(state));
  } catch {
    /* private mode */
  }
}

/** Banner only. The menu entry does not read this. */
export function shouldShowInstallBanner(now = Date.now()): boolean {
  if (isStandaloneDisplay()) return false;
  const state = readBannerSnooze();
  if (state.count >= INSTALL_BANNER_DISMISS_LIMIT) return false;
  return now >= state.until;
}

export function snoozeInstallBanner(now = Date.now()): void {
  const state = readBannerSnooze();
  const count = state.count + 1;
  writeBannerSnooze({
    count,
    until: count >= INSTALL_BANNER_DISMISS_LIMIT ? now : now + INSTALL_BANNER_SNOOZE_MS,
  });
}

export function isStandaloneDisplay(): boolean {
  const nav = navigator as Navigator & { standalone?: boolean };
  if (nav.standalone === true) return true;
  if (typeof window.matchMedia !== "function") return false;
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    window.matchMedia("(display-mode: fullscreen)").matches
  );
}

/** iPhone and iPad Safari. Other iOS browsers do not use the Share sheet hint. */
export function isIosSafari(): boolean {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent;
  const iosDevice =
    /iPad|iPhone|iPod/.test(ua) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  if (!iosDevice || !/WebKit/i.test(ua)) return false;
  if (/CriOS|FxiOS|EdgiOS|OPiOS|DuckDuckGo/i.test(ua)) return false;
  return /Safari/i.test(ua);
}

export function installPlatform(): InstallPlatform {
  if (typeof navigator === "undefined") return "desktop";
  const ua = navigator.userAgent;
  const iosDevice =
    /iPad|iPhone|iPod/.test(ua) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  if (iosDevice) return "ios";
  if (/Android/i.test(ua)) return "android";
  return "desktop";
}
