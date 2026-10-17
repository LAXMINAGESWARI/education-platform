import { render, screen, within } from "@testing-library/react";
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

function renderShell(path = "/subjects/sub-1") {
  return render(
    <MemoryRouter
      initialEntries={[path]}
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
        <Route
          path="/subjects/:subjectId/feedback"
          element={
            <AppShell>
              <div>Feedback body</div>
            </AppShell>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AppShell", () => {
  it("shows Home and Feedback in the student rail and keeps account actions in the profile menu", async () => {
    const user = userEvent.setup();
    renderShell();

    const rail = screen.getByRole("complementary", { name: "Primary" });
    expect(within(rail).getByRole("link", { name: "CurioEd" })).toBeInTheDocument();
    expect(within(rail).getByRole("link", { name: "Home" })).toHaveClass("is-active");
    expect(within(rail).getByRole("link", { name: "Feedback" })).not.toHaveClass("is-active");
    expect(screen.queryByText(/Demo mockup only/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reset demo" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Account menu" }));
    expect(screen.getByRole("menuitem", { name: "Reset demo" })).toBeInTheDocument();
    await user.click(screen.getByRole("menuitem", { name: "Sign out" }));
    expect(signOut).toHaveBeenCalled();
  });

  it("highlights Feedback instead of Home on a subject feedback page", () => {
    renderShell("/subjects/sub-1/feedback");

    const rail = screen.getByRole("complementary", { name: "Primary" });
    expect(within(rail).getByRole("link", { name: "Feedback" })).toHaveClass("is-active");
    expect(within(rail).getByRole("link", { name: "Home" })).not.toHaveClass("is-active");
  });
});
