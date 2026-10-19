import { describe, expect, it } from "vitest";

import {
  INSTALL_BANNER_DISMISS_LIMIT,
  INSTALL_BANNER_KEY,
  INSTALL_BANNER_SNOOZE_MS,
  installPlatform,
  isIosSafari,
  isStandaloneDisplay,
  shouldShowInstallBanner,
  snoozeInstallBanner,
} from "./installState";

const START = Date.parse("2026-10-19T00:00:00Z");

describe("install state", () => {
  it("snoozes the install banner for three days and stops after three dismissals", () => {
    localStorage.clear();
    expect(shouldShowInstallBanner(START)).toBe(true);

    snoozeInstallBanner(START);
    expect(shouldShowInstallBanner(START)).toBe(false);
    expect(shouldShowInstallBanner(START + INSTALL_BANNER_SNOOZE_MS - 1)).toBe(false);
    expect(shouldShowInstallBanner(START + INSTALL_BANNER_SNOOZE_MS)).toBe(true);

    snoozeInstallBanner(START + INSTALL_BANNER_SNOOZE_MS);
    expect(shouldShowInstallBanner(START + INSTALL_BANNER_SNOOZE_MS * 2)).toBe(true);

    snoozeInstallBanner(START + INSTALL_BANNER_SNOOZE_MS * 2);
    expect(JSON.parse(localStorage.getItem(INSTALL_BANNER_KEY) ?? "{}")).toMatchObject({
      count: INSTALL_BANNER_DISMISS_LIMIT,
    });
    expect(shouldShowInstallBanner(START + INSTALL_BANNER_SNOOZE_MS * 40)).toBe(false);
  });

  it("ignores a broken snooze record and still hides the banner in standalone mode", () => {
    localStorage.setItem(INSTALL_BANNER_KEY, "{");
    expect(shouldShowInstallBanner(START)).toBe(true);

    window.matchMedia = ((query: string) => ({
      matches: query === "(display-mode: standalone)",
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })) as unknown as typeof window.matchMedia;
    expect(isStandaloneDisplay()).toBe(true);
    expect(shouldShowInstallBanner(START)).toBe(false);

    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })) as unknown as typeof window.matchMedia;
    Object.defineProperty(navigator, "standalone", { configurable: true, value: true });
    expect(isStandaloneDisplay()).toBe(true);
    Object.defineProperty(navigator, "standalone", { configurable: true, value: undefined });
  });

  it("recognises iOS Safari and ignores desktop Chrome", () => {
    setNavigator(
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
      "iPhone",
      5,
    );
    expect(isIosSafari()).toBe(true);
    expect(installPlatform()).toBe("ios");

    setNavigator(
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/126.0.6478.54 Mobile/15E148 Safari/604.1",
      "iPhone",
      5,
    );
    expect(isIosSafari()).toBe(false);
    expect(installPlatform()).toBe("ios");

    setNavigator(
      "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.122 Mobile Safari/537.36",
      "Linux armv8l",
      5,
    );
    expect(installPlatform()).toBe("android");

    setNavigator(
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
      "Win32",
      0,
    );
    expect(isIosSafari()).toBe(false);
    expect(installPlatform()).toBe("desktop");
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
