import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MobileNav } from "./MobileNav";

function renderNav() {
  return render(
    <MemoryRouter>
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

describe("MobileNav", () => {
  afterEach(() => {
    localStorage.clear();
    Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, value: 0 });
  });

  it("keeps the drawer closed until the menu button is used", async () => {
    const user = userEvent.setup();
    renderNav();

    expect(screen.queryByRole("complementary", { name: "Student" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open menu" }));

    const drawer = screen.getByRole("complementary", { name: "Student" });
    expect(within(drawer).getByRole("link", { name: "Home" })).toHaveAttribute("aria-current", "page");
    expect(within(drawer).getByRole("link", { name: "Feedback" })).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("complementary", { name: "Student" })).not.toBeInTheDocument();
  });

  it("keeps haptic feedback in the phone drawer and persists the choice", async () => {
    Object.defineProperty(navigator, "vibrate", { configurable: true, value: () => true });
    Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, value: 1 });
    localStorage.clear();
    const user = userEvent.setup();
    const view = renderNav();

    await user.click(screen.getByRole("button", { name: "Open menu" }));
    const drawer = screen.getByRole("complementary", { name: "Student" });
    const toggle = within(drawer).getByRole("switch", { name: "Haptic feedback" });
    expect(toggle).toBeChecked();

    await user.click(toggle);
    expect(toggle).not.toBeChecked();
    expect(localStorage.getItem("ep.haptics")).toBe("off");

    view.unmount();
    renderNav();
    await user.click(screen.getByRole("button", { name: "Open menu" }));
    expect(screen.getByRole("switch", { name: "Haptic feedback" })).not.toBeChecked();
  });

  it("hides haptic feedback when the device cannot buzz", async () => {
    Object.defineProperty(navigator, "vibrate", { configurable: true, value: () => true });
    Object.defineProperty(navigator, "maxTouchPoints", { configurable: true, value: 0 });
    window.matchMedia = vi.fn((query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })) as unknown as typeof window.matchMedia;
    const user = userEvent.setup();
    renderNav();
    await user.click(screen.getByRole("button", { name: "Open menu" }));
    expect(screen.queryByRole("switch", { name: "Haptic feedback" })).not.toBeInTheDocument();
  });
});
