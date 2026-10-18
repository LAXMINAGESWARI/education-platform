import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

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
});
