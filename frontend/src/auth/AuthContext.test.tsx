import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, useAuth } from "./AuthContext";
import type { EnrollmentSummary, MeResponse } from "../api/types";

const login = vi.fn();
const fetchMe = vi.fn();
const fetchEnrollments = vi.fn();
const getAccessToken = vi.fn();

vi.mock("../api/auth", () => ({
  login: (...args: unknown[]) => login(...args),
  fetchMe: (...args: unknown[]) => fetchMe(...args),
  logout: vi.fn(),
  provisionStudent: vi.fn(),
}));

vi.mock("../api/client", () => ({
  getAccessToken: () => getAccessToken(),
  getRefreshToken: () => null,
  clearTokens: vi.fn(),
}));

vi.mock("../api/enrollments", () => ({
  fetchEnrollments: (...args: unknown[]) => fetchEnrollments(...args),
  hasActiveSubjectEnrollment: (summary: EnrollmentSummary) =>
    summary.subject_enrollments.some((row) => row.status === "active"),
}));

function me(over: Partial<MeResponse>): MeResponse {
  return {
    id: "u1",
    email: "user@demo.school",
    full_name: "Demo User",
    institution_id: "inst",
    roles: ["student"],
    student_profile_id: "sp1",
    status: "active",
    ...over,
  };
}

function Probe() {
  const { user, enrollments, loading, signIn } = useAuth();
  return (
    <div>
      <div data-testid="loading">{String(loading)}</div>
      <div data-testid="email">{user?.email ?? ""}</div>
      <div data-testid="enrolled">{enrollments ? "yes" : "no"}</div>
      <button
        type="button"
        onClick={() => void signIn("meera.krishnan@alnoor.school", "demo1234")}
      >
        Sign in teacher
      </button>
      <button type="button" onClick={() => void signIn("student@demo.school", "demo1234")}>
        Sign in student
      </button>
    </div>
  );
}

function renderAuth() {
  return render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  );
}

describe("AuthProvider signIn", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    getAccessToken.mockReturnValue(null);
    login.mockResolvedValue({
      access_token: "a",
      refresh_token: "r",
      token_type: "bearer",
    });
  });

  it("signs a teacher in without calling student enrollments", async () => {
    fetchMe.mockResolvedValue(
      me({
        email: "meera.krishnan@alnoor.school",
        roles: ["teacher"],
        student_profile_id: null,
      }),
    );
    fetchEnrollments.mockRejectedValue(new Error("Student profile required"));

    renderAuth();
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));

    await userEvent.click(screen.getByRole("button", { name: "Sign in teacher" }));

    await waitFor(() =>
      expect(screen.getByTestId("email")).toHaveTextContent("meera.krishnan@alnoor.school"),
    );
    expect(fetchEnrollments).not.toHaveBeenCalled();
    expect(screen.getByTestId("enrolled")).toHaveTextContent("no");
  });

  it("still loads enrollments for a student", async () => {
    fetchMe.mockResolvedValue(me({ email: "student@demo.school" }));
    fetchEnrollments.mockResolvedValue({
      grade_enrollments: [],
      subject_enrollments: [],
    });

    renderAuth();
    await waitFor(() => expect(screen.getByTestId("loading")).toHaveTextContent("false"));

    await userEvent.click(screen.getByRole("button", { name: "Sign in student" }));

    await waitFor(() =>
      expect(screen.getByTestId("email")).toHaveTextContent("student@demo.school"),
    );
    expect(fetchEnrollments).toHaveBeenCalledOnce();
  });
});
