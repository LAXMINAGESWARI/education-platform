import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { resetInstallPromptState } from "../pwa/installPrompt";
import { INSTALL_BANNER_KEY } from "../pwa/installState";
import { MobileNav } from "./MobileNav";

describe("Install app menu entry", () => {
  beforeEach(() => {
    localStorage.clear();
    resetInstallPromptState();
    setUserAgent(
      "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.122 Mobile Safari/537.36",
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

  it("stays in the phone menu after the banner is exhausted and opens instructions", async () => {
    localStorage.setItem(INSTALL_BANNER_KEY, JSON.stringify({ count: 3, until: 0 }));
    const user = userEvent.setup();
    renderNav();

    await user.click(screen.getByRole("button", { name: "Open menu" }));
    const install = screen.getByRole("button", { name: "Install app" });
    expect(install).not.toHaveAttribute("data-haptics", "off");

    await user.click(install);
    const sheet = screen.getByRole("dialog", { name: "Install CurioEd" });
    expect(sheet).toHaveTextContent("Android Chrome: open the menu (⋮), then tap Install app or Add to Home screen.");
    expect(sheet).toHaveTextContent("iPhone Safari: tap Share, then Add to Home Screen.");
    expect(sheet).toHaveTextContent("Desktop Chrome or Edge: use the install icon in the address bar.");

    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog", { name: "Install CurioEd" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Install app" })).toBeInTheDocument();
  });

  it("calls the deferred install prompt instead of the instruction sheet", async () => {
    const user = userEvent.setup();
    renderNav();
    await user.click(screen.getByRole("button", { name: "Open menu" }));

    const prompt = vi.fn(async () => undefined);
    actInstallEvent(prompt, "accepted");
    await user.click(screen.getByRole("button", { name: "Install app" }));

    expect(prompt).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Install app" })).not.toBeInTheDocument();
  });

  it("hides the menu entry in standalone mode and after appinstalled", async () => {
    const user = userEvent.setup();
    window.matchMedia = ((query: string) => ({
      matches: query === "(display-mode: standalone)",
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })) as unknown as typeof window.matchMedia;

    const view = renderNav();
    await user.click(screen.getByRole("button", { name: "Open menu" }));
    expect(screen.queryByRole("button", { name: "Install app" })).not.toBeInTheDocument();

    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })) as unknown as typeof window.matchMedia;
    view.unmount();
    renderNav();
    await user.click(screen.getByRole("button", { name: "Open menu" }));
    expect(screen.getByRole("button", { name: "Install app" })).toBeInTheDocument();

    act(() => {
      window.dispatchEvent(new Event("appinstalled"));
    });
    expect(screen.queryByRole("button", { name: "Install app" })).not.toBeInTheDocument();
  });
});

function renderNav() {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <MobileNav
        brandTo="/"
        navLabel="Student"
        items={[
          { to: "/", label: "Home", active: true },
          { to: "/feedback", label: "Feedback", active: false },
        ]}
      />
    </MemoryRouter>,
  );
}

function setUserAgent(userAgent: string) {
  Object.defineProperty(navigator, "userAgent", { configurable: true, value: userAgent });
}

function actInstallEvent(prompt: () => Promise<void>, outcome: "accepted" | "dismissed") {
  const event = new Event("beforeinstallprompt", { cancelable: true });
  Object.assign(event, {
    prompt,
    userChoice: Promise.resolve({ outcome, platform: "web" }),
  });
  act(() => {
    window.dispatchEvent(event);
  });
}
