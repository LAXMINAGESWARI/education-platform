import { describe, expect, it } from "vitest";

import { isApiBaseUrl, isSensitivePath, mustUseNetworkOnly, NAVIGATION_DENYLIST } from "./cachePolicy";

describe("cache policy", () => {
  it("treats API, auth, health, and docs as sensitive", () => {
    expect(isSensitivePath("/api/v1/auth/refresh")).toBe(true);
    expect(isSensitivePath("/auth/session")).toBe(true);
    expect(isSensitivePath("/health")).toBe(true);
    expect(isSensitivePath("/docs")).toBe(true);
    expect(isSensitivePath("/docs/oauth2-redirect")).toBe(true);
    expect(isSensitivePath("/redoc")).toBe(true);
    expect(isSensitivePath("/openapi.json")).toBe(true);
    expect(isSensitivePath("/login")).toBe(false);
    expect(isSensitivePath("/icons/icon-192.png")).toBe(false);
  });

  it("matches a configured API origin without swallowing other hosts", () => {
    const api = "https://api.curioed.online/api/v1";
    expect(isApiBaseUrl(new URL("https://api.curioed.online/api/v1/auth/refresh"), api)).toBe(true);
    expect(isApiBaseUrl(new URL("https://api.curioed.online/static/logo.png"), api)).toBe(false);
    expect(isApiBaseUrl(new URL("https://evil.example/api/v1/auth/refresh"), api)).toBe(false);
    expect(isApiBaseUrl(new URL("https://curioed.online/api/v1/me"), "")).toBe(false);
  });

  it("keeps authorized and same-origin API calls on the network", () => {
    const page = "https://curioed.online";
    expect(
      mustUseNetworkOnly(new URL("https://curioed.online/api/v1/me"), null, page, ""),
    ).toBe(true);
    expect(
      mustUseNetworkOnly(new URL("https://curioed.online/icons/icon-192.png"), "Bearer secret", page, ""),
    ).toBe(true);
    expect(
      mustUseNetworkOnly(new URL("https://curioed.online/icons/icon-192.png"), null, page, ""),
    ).toBe(false);
    expect(
      mustUseNetworkOnly(
        new URL("http://127.0.0.1:8000/api/v1/auth/login"),
        null,
        page,
        "http://127.0.0.1:8000",
      ),
    ).toBe(true);
  });

  it("denies app-shell fallback for API navigations", () => {
    const denied = (path: string) => NAVIGATION_DENYLIST.some((pattern) => pattern.test(path));
    expect(denied("/api/v1/auth/refresh")).toBe(true);
    expect(denied("/health")).toBe(true);
    expect(denied("/openapi.json")).toBe(true);
    expect(denied("/login")).toBe(false);
    expect(denied("/quizzes/1")).toBe(false);
  });
});
