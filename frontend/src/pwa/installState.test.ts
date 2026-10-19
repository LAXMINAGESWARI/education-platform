import { describe, expect, it } from "vitest";

import {
  IOS_HINT_DISMISS_KEY,
  isIosSafari,
  isStandaloneDisplay,
  readDismissed,
  rememberDismissed,
} from "./installState";

describe("install state", () => {
  it("remembers a dismissal", () => {
    localStorage.clear();
    expect(readDismissed(IOS_HINT_DISMISS_KEY)).toBe(false);
    rememberDismissed(IOS_HINT_DISMISS_KEY);
    expect(readDismissed(IOS_HINT_DISMISS_KEY)).toBe(true);
  });

  it("recognises iOS Safari and ignores desktop Chrome", () => {
    setNavigator(
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
      "iPhone",
      5,
    );
    expect(isIosSafari()).toBe(true);

    setNavigator(
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/126.0.6478.54 Mobile/15E148 Safari/604.1",
      "iPhone",
      5,
    );
    expect(isIosSafari()).toBe(false);

    setNavigator(
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
      "Win32",
      0,
    );
    expect(isIosSafari()).toBe(false);
  });

  it("reads standalone display mode", () => {
    window.matchMedia = ((query: string) => ({
      matches: query === "(display-mode: standalone)",
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })) as unknown as typeof window.matchMedia;
    expect(isStandaloneDisplay()).toBe(true);
  });
});

function setNavigator(userAgent: string, platform: string, maxTouchPoints: number) {
  Object.defineProperty(navigator, "userAgent", { configurable: true, value: userAgent });
  Object.defineProperty(navigator, "platform", { configurable: true, value: platform });
  Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, value: maxTouchPoints });
}
