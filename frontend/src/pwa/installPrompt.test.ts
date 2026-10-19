import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { snoozeInstallBanner } from "./installState";
import {
  ensureInstallPromptListener,
  getInstallPrompt,
  promptInstall,
  resetInstallPromptState,
  wasInstalled,
} from "./installPrompt";

describe("install prompt capture", () => {
  beforeEach(() => {
    localStorage.clear();
    resetInstallPromptState();
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })) as unknown as typeof window.matchMedia;
  });

  afterEach(() => {
    resetInstallPromptState();
  });

  it("keeps beforeinstallprompt after the banner is snoozed", async () => {
    ensureInstallPromptListener();
    const prompt = vi.fn(async () => undefined);
    dispatchInstallEvent(prompt);

    snoozeInstallBanner();
    expect(getInstallPrompt()).not.toBeNull();

    await promptInstall();
    expect(prompt).toHaveBeenCalledOnce();
    expect(getInstallPrompt()).toBeNull();
    expect(wasInstalled()).toBe(false);
  });

  it("hides install entry points after appinstalled", () => {
    ensureInstallPromptListener();
    dispatchInstallEvent(vi.fn(async () => undefined));
    window.dispatchEvent(new Event("appinstalled"));
    expect(wasInstalled()).toBe(true);
    expect(getInstallPrompt()).toBeNull();
  });
});

function dispatchInstallEvent(prompt: () => Promise<void>) {
  const event = new Event("beforeinstallprompt", { cancelable: true });
  Object.assign(event, {
    prompt,
    userChoice: Promise.resolve({ outcome: "dismissed", platform: "web" }),
  });
  window.dispatchEvent(event);
}
