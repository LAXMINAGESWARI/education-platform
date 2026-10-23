import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminShell } from "./AdminShell";

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    user: { full_name: "Admin Demo" },
    signOut: vi.fn(),
    isDevMockSession: false,
  }),
}));

describe("AdminShell", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("marks the app collapsed so content can reclaim rail space", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/admin/policy"]}>
        <Routes>
          <Route path="/admin" element={<AdminShell />}>
            <Route path="policy" element={<p>Policy body</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getAllByRole("link", { name: "CurioEd" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "Sign out" }).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Demo mockup only/)).not.toBeInTheDocument();
    expect(document.querySelector(".demo-banner")).not.toBeInTheDocument();
    expect(document.getElementById("pwa-prompt-host")).not.toBeNull();

    const app = document.querySelector(".app");
    expect(app).not.toHaveClass("is-rail-collapsed");

    await user.click(screen.getByRole("button", { name: "Collapse navigation" }));
    expect(app).toHaveClass("is-rail-collapsed");

    await user.click(screen.getByRole("button", { name: "Expand navigation" }));
    expect(app).not.toHaveClass("is-rail-collapsed");
  });
});
