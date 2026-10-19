import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { TeacherShell } from "./TeacherShell";

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    user: { full_name: "Meera Krishnan" },
    signOut: vi.fn(),
    isDevMockSession: false,
  }),
}));

describe("TeacherShell", () => {
  it("keeps the top bar and omits the status banner", () => {
    render(
      <MemoryRouter initialEntries={["/teacher"]}>
        <Routes>
          <Route path="/teacher" element={<TeacherShell />}>
            <Route index element={<p>Class list</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getAllByRole("link", { name: "CurioEd" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "Sign out" }).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Teacher workspace/)).not.toBeInTheDocument();
    expect(document.querySelector(".demo-banner")).not.toBeInTheDocument();
    expect(screen.getByText("Class list")).toBeInTheDocument();
  });
});
