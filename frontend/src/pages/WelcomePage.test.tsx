import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DEMO_PASSWORD } from "../auth/demoAccounts";
import { WelcomePage } from "./WelcomePage";

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

function renderPage() {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <WelcomePage />
    </MemoryRouter>,
  );
}

describe("WelcomePage demo accounts", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    signIn.mockResolvedValue(undefined);
  });

  it("lists the seeded Al Noor accounts and fills the form from a click", async () => {
    renderPage();

    expect(screen.getByText("CurioEd")).toBeInTheDocument();
    expect(document.querySelector(".route-motion__page")).not.toBeNull();
    expect(screen.queryByText(/Demo mockup only/)).not.toBeInTheDocument();
    expect(screen.queryByText(/demo\.school/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Quick demo/i })).not.toBeInTheDocument();
    expect(screen.getByText("Adnan, Grade 6")).toBeInTheDocument();
    expect(screen.getByText("Aisha Rahman, Grade 8")).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: /fatima\.almansouri@alnoor\.school/ }),
    );

    expect(screen.getByLabelText("Email")).toHaveValue("fatima.almansouri@alnoor.school");
    expect(screen.getByLabelText("Password")).toHaveValue(DEMO_PASSWORD);
  });

  it("signs the seeded teacher in with a real JWT instead of a fixture session", async () => {
    renderPage();

    await userEvent.click(screen.getByRole("button", { name: "Enter as teacher" }));

    expect(signIn).toHaveBeenCalledWith("meera.krishnan@alnoor.school", "demo1234");
    expect(navigate).toHaveBeenCalledWith("/teacher", { replace: true });
  });

  it("signs in the seeded admin and both students from the dev shortcuts", async () => {
    renderPage();

    await userEvent.click(screen.getByRole("button", { name: "Enter as admin" }));
    expect(signIn).toHaveBeenCalledWith("fatima.almansouri@alnoor.school", "demo1234");
    expect(navigate).toHaveBeenCalledWith("/admin", { replace: true });

    await userEvent.click(screen.getByRole("button", { name: "Enter as student" }));
    expect(signIn).toHaveBeenCalledWith("student1@alnoor.school", "demo1234");
    expect(navigate).toHaveBeenCalledWith("/", { replace: true });

    await userEvent.click(
      screen.getByRole("button", { name: "Enter as second student" }),
    );
    expect(signIn).toHaveBeenCalledWith("student97@alnoor.school", "demo1234");
  });
});
