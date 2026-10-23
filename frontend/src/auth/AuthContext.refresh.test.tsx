import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "./AuthContext";
import { RequireAuth } from "./RequireAuth";

const ACCESS_KEY = "ep_access_token";
const REFRESH_KEY = "ep_refresh_token";

const teacher = {
  id: "u1",
  email: "meera.krishnan@alnoor.school",
  full_name: "Meera Krishnan",
  institution_id: "inst",
  roles: ["teacher"],
  student_profile_id: null,
  status: "active",
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  return input.url;
}

type RefreshFailure = "network" | 503 | 401 | 403;

function installFetch(failure: RefreshFailure) {
  let retry = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url.endsWith("/auth/me")) {
        if (retry) return json(200, teacher);
        return json(401, { detail: "Unauthorized" });
      }
      if (url.endsWith("/auth/refresh")) {
        if (failure === "network") throw new TypeError("Failed to fetch");
        if (failure === 503) return json(503, { detail: "unavailable" });
        if (failure === 403) return json(403, { detail: "Forbidden" });
        return json(401, { detail: "Unauthorized" });
      }
      throw new Error(`unexpected ${url}`);
    }),
  );
  return {
    allowRetry() {
      retry = true;
    },
  };
}

function renderColdStart() {
  return render(
    <MemoryRouter
      initialEntries={["/home"]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<div>Login page</div>} />
          <Route
            path="/home"
            element={
              <RequireAuth>
                <div>Home for {teacher.email}</div>
              </RequireAuth>
            }
          />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("AuthProvider cold start refresh", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem(ACCESS_KEY, "a-1");
    localStorage.setItem(REFRESH_KEY, "r-1");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it.each([
    ["network", "network"],
    ["5xx", 503],
  ] as const)("keeps the tokens and the user when refresh fails with a %s", async (_label, failure) => {
    const fetchMock = installFetch(failure);
    const user = userEvent.setup();
    renderColdStart();

    expect(await screen.findByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Can't reach the server. Retry to continue.");
    expect(screen.queryByText("Login page")).not.toBeInTheDocument();
    expect(localStorage.getItem(ACCESS_KEY)).toBe("a-1");
    expect(localStorage.getItem(REFRESH_KEY)).toBe("r-1");

    fetchMock.allowRetry();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(`Home for ${teacher.email}`)).toBeInTheDocument();
    expect(localStorage.getItem(REFRESH_KEY)).toBe("r-1");
  });

  it.each([
    [401, 401],
    [403, 403],
  ] as const)("signs the user out when refresh returns %s", async (_label, status) => {
    installFetch(status);
    renderColdStart();

    expect(await screen.findByText("Login page")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(screen.queryByText(`Home for ${teacher.email}`)).not.toBeInTheDocument();
    await waitFor(() => {
      expect(localStorage.getItem(ACCESS_KEY)).toBeNull();
      expect(localStorage.getItem(REFRESH_KEY)).toBeNull();
    });
  });
});
