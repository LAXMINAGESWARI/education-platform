import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "./AppShell";

const signOut = vi.fn();

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    user: { full_name: "Asha Student" },
    enrollments: {
      grade_enrollments: [{ status: "active", grade_name: "Grade 8" }],
      subject_enrollments: [{ status: "active", subject_name: "Mathematics" }],
    },
    enrolled: true,
    signOut,
  }),
}));

vi.mock("../api/demo", () => ({
  resetDemoProgress: vi.fn(),
}));

function renderShell() {
  return render(
    <MemoryRouter
      initialEntries={["/subjects/sub-1"]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route
          path="/subjects/:subjectId"
          element={
            <AppShell>
              <div>Subject body</div>
            </AppShell>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AppShell", () => {
  it("keeps reset and sign out under a persistent profile icon", async () => {
    const user = userEvent.setup();
    renderShell();

    expect(screen.queryByRole("complementary", { name: "Primary" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "CurioEd" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Demo mockup only/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reset demo" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Account menu" }));
    expect(screen.getByRole("menuitem", { name: "Reset demo" })).toBeInTheDocument();
    await user.click(screen.getByRole("menuitem", { name: "Sign out" }));
    expect(signOut).toHaveBeenCalled();
  });
});
