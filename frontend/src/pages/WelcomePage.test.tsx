import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const signIn = vi.fn();
const navigate = vi.fn();

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    user: null,
    loading: false,
    signIn,
    setEnrollments: vi.fn(),
  }),
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => navigate,
  };
});

import { WelcomePage } from "./WelcomePage";

describe("WelcomePage teacher shortcut", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    signIn.mockResolvedValue(undefined);
  });

  it("signs the seeded teacher in with a real JWT instead of a fixture session", async () => {
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <WelcomePage />
      </MemoryRouter>,
    );

    expect(screen.getByText("CurioEd")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Enter as teacher" }));

    expect(signIn).toHaveBeenCalledWith("meera.krishnan@alnoor.school", "demo1234");
    expect(navigate).toHaveBeenCalledWith("/teacher", { replace: true });
  });
});
