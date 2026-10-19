import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetInstallPromptState } from "../pwa/installPrompt";
import { INSTALL_DISMISS_KEY, IOS_HINT_DISMISS_KEY } from "../pwa/installState";
import { PwaPrompts } from "./PwaPrompts";

const sw = vi.hoisted(() => ({
  needRefresh: false,
  updateServiceWorker: vi.fn(async () => undefined),
}));

vi.mock("virtual:pwa-register/react", () => ({
  useRegisterSW: () => ({
    needRefresh: [sw.needRefresh, vi.fn()] as const,
    offlineReady: [false, vi.fn()] as const,
    updateServiceWorker: sw.updateServiceWorker,
  }),
}));

const IOS_SAFARI =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1";

describe("PwaPrompts", () => {
  beforeEach(() => {
    localStorage.clear();
    resetInstallPromptState();
    sw.needRefresh = false;
    sw.updateServiceWorker.mockClear();
    setUserAgent(
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    );
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

  it("shows an install button from beforeinstallprompt and remembers a dismissal", async () => {
    const user = userEvent.setup();
    renderPrompt();

    const prompt = vi.fn(async () => undefined);
    actInstallEvent(prompt);
    const install = screen.getByRole("button", { name: "Install app" });
    expect(install).not.toHaveAttribute("data-haptics", "off");

    await user.click(screen.getByRole("button", { name: "Not now" }));
    expect(localStorage.getItem(INSTALL_DISMISS_KEY)).toBe("1");
    expect(screen.queryByRole("button", { name: "Install app" })).not.toBeInTheDocument();

    renderPrompt();
    actInstallEvent(prompt);
    expect(screen.queryByRole("button", { name: "Install app" })).not.toBeInTheDocument();
  });

  it("calls the browser install prompt", async () => {
    const user = userEvent.setup();
    renderPrompt();
    const prompt = vi.fn(async () => undefined);
    actInstallEvent(prompt, "dismissed");
    await user.click(screen.getByRole("button", { name: "Install app" }));
    expect(prompt).toHaveBeenCalledOnce();
    expect(localStorage.getItem(INSTALL_DISMISS_KEY)).toBe("1");
  });

  it("shows the iOS Share hint once", async () => {
    setUserAgent(IOS_SAFARI);
    const user = userEvent.setup();
    renderPrompt();
    expect(screen.getByRole("region", { name: "Install CurioEd" })).toHaveTextContent(
      "tap Share, then Add to Home Screen",
    );

    await user.click(screen.getByRole("button", { name: "Not now" }));
    expect(localStorage.getItem(IOS_HINT_DISMISS_KEY)).toBe("1");
    expect(screen.queryByRole("region", { name: "Install CurioEd" })).not.toBeInTheDocument();

    renderPrompt();
    expect(screen.queryByRole("region", { name: "Install CurioEd" })).not.toBeInTheDocument();
  });

  it("hides install hints in standalone mode", () => {
    setUserAgent(IOS_SAFARI);
    window.matchMedia = ((query: string) => ({
      matches: query === "(display-mode: standalone)",
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })) as unknown as typeof window.matchMedia;
    renderPrompt();
    expect(screen.queryByRole("region", { name: "Install CurioEd" })).not.toBeInTheDocument();
  });

  it("offers a reload when a new service worker is waiting", async () => {
    sw.needRefresh = true;
    const user = userEvent.setup();
    renderPrompt();
    expect(screen.getByRole("status")).toHaveTextContent("A new version of CurioEd is available.");
    await user.click(screen.getByRole("button", { name: "Reload" }));
    expect(sw.updateServiceWorker).toHaveBeenCalledWith(true);
    await user.click(screen.getByRole("button", { name: "Later" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

});

function renderPrompt() {
  return render(<PwaPrompts />);
}

function setUserAgent(userAgent: string) {
  Object.defineProperty(navigator, "userAgent", { configurable: true, value: userAgent });
}

function actInstallEvent(prompt: () => Promise<void>, outcome: "accepted" | "dismissed" = "accepted") {
  const event = new Event("beforeinstallprompt", { cancelable: true });
  Object.assign(event, {
    prompt,
    userChoice: Promise.resolve({ outcome, platform: "web" }),
  });
  act(() => {
    window.dispatchEvent(event);
  });
}
