import { act, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getInstallPrompt, resetInstallPromptState } from "../pwa/installPrompt";
import * as serviceWorkerUpdate from "../pwa/serviceWorkerUpdate";
import {
  INSTALL_BANNER_DISMISS_LIMIT,
  INSTALL_BANNER_KEY,
  INSTALL_BANNER_SNOOZE_MS,
} from "../pwa/installState";
import { PwaPromptHost, PwaPromptRegistry, PwaPrompts } from "./PwaPrompts";

const sw = vi.hoisted(() => ({
  needRefresh: false,
  onNeedReload: undefined as (() => void) | undefined,
  updateServiceWorker: vi.fn(async () => undefined),
}));

vi.mock("virtual:pwa-register/react", () => ({
  useRegisterSW: (options?: { onNeedReload?: () => void }) => {
    sw.onNeedReload = options?.onNeedReload;
    return {
      needRefresh: [sw.needRefresh, vi.fn()] as const,
      offlineReady: [false, vi.fn()] as const,
      updateServiceWorker: sw.updateServiceWorker,
    };
  },
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

  it("snoozes the install banner without dropping the browser prompt", async () => {
    const user = userEvent.setup();
    const view = renderPrompt();

    const prompt = vi.fn(async () => undefined);
    actInstallEvent(prompt);
    const install = screen.getByRole("button", { name: "Install app" });
    expect(install).not.toHaveAttribute("data-haptics", "off");

    await user.click(screen.getByRole("button", { name: "Not now" }));
    expect(screen.queryByRole("button", { name: "Install app" })).not.toBeInTheDocument();
    expect(getInstallPrompt()).not.toBeNull();

    const stored = JSON.parse(localStorage.getItem(INSTALL_BANNER_KEY) ?? "{}") as { until: number };
    localStorage.setItem(INSTALL_BANNER_KEY, JSON.stringify({ count: 1, until: stored.until - INSTALL_BANNER_SNOOZE_MS }));
    view.rerender(promptAt());
    expect(screen.getByRole("button", { name: "Install app" })).toBeInTheDocument();
  });

  it("stops the banner after three dismissals", async () => {
    const user = userEvent.setup();
    const view = renderPrompt();
    actInstallEvent(vi.fn(async () => undefined));

    for (let dismissals = 1; dismissals <= INSTALL_BANNER_DISMISS_LIMIT; dismissals += 1) {
      await user.click(screen.getByRole("button", { name: "Not now" }));
      expect(screen.queryByRole("region", { name: "Install CurioEd" })).not.toBeInTheDocument();
      if (dismissals === INSTALL_BANNER_DISMISS_LIMIT) break;
      const stored = JSON.parse(localStorage.getItem(INSTALL_BANNER_KEY) ?? "{}") as { count: number };
      localStorage.setItem(INSTALL_BANNER_KEY, JSON.stringify({ count: stored.count, until: 0 }));
      view.rerender(promptAt());
    }

    localStorage.setItem(
      INSTALL_BANNER_KEY,
      JSON.stringify({ count: INSTALL_BANNER_DISMISS_LIMIT, until: 0 }),
    );
    view.rerender(promptAt());
    expect(screen.queryByRole("region", { name: "Install CurioEd" })).not.toBeInTheDocument();
  });

  it("calls the browser install prompt without snoozing the banner", async () => {
    const user = userEvent.setup();
    renderPrompt();
    const prompt = vi.fn(async () => undefined);
    actInstallEvent(prompt, "dismissed");
    await user.click(screen.getByRole("button", { name: "Install app" }));
    expect(prompt).toHaveBeenCalledOnce();
    expect(localStorage.getItem(INSTALL_BANNER_KEY)).toBeNull();
  });

  it("snoozes the iOS Share hint and shows it again after three days", async () => {
    setUserAgent(IOS_SAFARI);
    const user = userEvent.setup();
    const view = renderPrompt();
    expect(screen.getByRole("region", { name: "Install CurioEd" })).toHaveTextContent(
      "tap Share, then Add to Home Screen",
    );

    await user.click(screen.getByRole("button", { name: "Not now" }));
    expect(screen.queryByRole("region", { name: "Install CurioEd" })).not.toBeInTheDocument();

    view.rerender(promptAt());
    expect(screen.queryByRole("region", { name: "Install CurioEd" })).not.toBeInTheDocument();

    const stored = JSON.parse(localStorage.getItem(INSTALL_BANNER_KEY) ?? "{}") as { until: number };
    localStorage.setItem(INSTALL_BANNER_KEY, JSON.stringify({ count: 1, until: stored.until - INSTALL_BANNER_SNOOZE_MS }));
    view.rerender(promptAt());
    expect(screen.getByRole("region", { name: "Install CurioEd" })).toBeInTheDocument();
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

  it("flows the install banner above the page title instead of covering it", async () => {
    setUserAgent(IOS_SAFARI);
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <PwaPromptRegistry>
          <div className="app__content">
            <PwaPromptHost />
            <header className="page-head">
              <nav aria-label="Breadcrumb">Mathematics</nav>
              <h1>Topic generation</h1>
            </header>
          </div>
          <PwaPrompts />
        </PwaPromptRegistry>
      </MemoryRouter>,
    );

    await waitFor(() => {
      const banner = screen.getByRole("region", { name: "Install CurioEd" });
      expect(banner.closest(".pwa-toasts")).toHaveClass("pwa-toasts--inline");
      expect(document.getElementById("pwa-prompt-host")).toContainElement(banner);
    });
    const banner = screen.getByRole("region", { name: "Install CurioEd" });
    const title = screen.getByRole("heading", { name: "Topic generation" });
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(banner.closest(".app__content")).toBe(title.closest(".app__content"));
    expect(banner.compareDocumentPosition(crumbs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(banner.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("moves the banner into the host when the shell mounts after auth without a route change", async () => {
    setUserAgent(IOS_SAFARI);
    function ShellAfterAuth() {
      const [ready, setReady] = useState(false);
      if (!ready) {
        return (
          <button type="button" onClick={() => setReady(true)}>
            Finish auth
          </button>
        );
      }
      return (
        <div className="app__content">
          <PwaPromptHost />
          <nav aria-label="Breadcrumb">Mathematics</nav>
        </div>
      );
    }

    const user = userEvent.setup();
    render(
      <MemoryRouter
        initialEntries={["/teacher/topics/topic-1/generate"]}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <PwaPromptRegistry>
          <PwaPrompts />
          <ShellAfterAuth />
        </PwaPromptRegistry>
      </MemoryRouter>,
    );

    const banner = await screen.findByRole("region", { name: "Install CurioEd" });
    expect(banner.closest("#pwa-prompt-host")).toBeNull();
    expect(screen.getByRole("button", { name: "Finish auth" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Finish auth" }));
    await waitFor(() => {
      expect(document.getElementById("pwa-prompt-host")).toContainElement(
        screen.getByRole("region", { name: "Install CurioEd" }),
      );
    });
    expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toHaveTextContent("Mathematics");
    expect(screen.queryByRole("button", { name: "Finish auth" })).not.toBeInTheDocument();
  });

  it("reloads as soon as a new service worker activates outside a quiz", () => {
    const reload = vi.spyOn(serviceWorkerUpdate, "reloadApp").mockImplementation(() => undefined);
    renderPrompt("/");
    act(() => sw.onNeedReload?.());
    expect(reload).toHaveBeenCalledOnce();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("holds the service worker reload while a quiz is open", async () => {
    const reload = vi.spyOn(serviceWorkerUpdate, "reloadApp").mockImplementation(() => undefined);
    const user = userEvent.setup();
    renderPrompt("/quizzes/overall-1");
    act(() => sw.onNeedReload?.());
    expect(reload).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("A new version of CurioEd is available.");
    await user.click(screen.getByRole("button", { name: "Reload" }));
    expect(reload).toHaveBeenCalledOnce();
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

function promptAt(path = "/") {
  return (
    <MemoryRouter
      initialEntries={[path]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <PwaPrompts />
    </MemoryRouter>
  );
}

function renderPrompt(path = "/") {
  return render(promptAt(path));
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
