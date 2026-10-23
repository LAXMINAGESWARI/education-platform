import { ApiError, type TokenResponse } from "./types";

const ACCESS_KEY = "ep_access_token";
const REFRESH_KEY = "ep_refresh_token";

export function getApiBaseUrl(): string {
  return (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ??
    "http://127.0.0.1:8000";
}

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_KEY);
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY);
}

export function setTokens(tokens: TokenResponse): void {
  localStorage.setItem(ACCESS_KEY, tokens.access_token);
  localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

type RequestOptions = {
  method?: string;
  /** JSON-serializable value, or FormData for multipart uploads (no Content-Type set). */
  body?: unknown;
  auth?: boolean;
  skipRefresh?: boolean;
  signal?: AbortSignal;
  headers?: Record<string, string>;
};

function isFormDataBody(body: unknown): body is FormData {
  return typeof FormData !== "undefined" && body instanceof FormData;
}

let refreshPromise: Promise<boolean> | null = null;

/**
 * One small POST. Ten seconds covers a slow mobile round trip and is shorter
 * than the browser's default fetch hang, so a stalled auth server cannot pin
 * every 401 caller. The timer uses its own AbortController, never a caller's.
 */
export const REFRESH_TIMEOUT_MS = 10_000;

function abortError(): DOMException {
  return new DOMException("Aborted", "AbortError");
}

function isTokenResponse(value: unknown): value is TokenResponse {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.access_token === "string" &&
    record.access_token.length > 0 &&
    typeof record.refresh_token === "string" &&
    record.refresh_token.length > 0 &&
    typeof record.token_type === "string"
  );
}

/**
 * Shared refresh. No caller signal: a rotated pair must be saved even if a waiter gives up.
 * Tokens are cleared only when /auth/refresh rejects the session (401 or 403).
 * The refresh route returns 401 for an invalid, expired, or revoked token.
 */
async function performRefresh(): Promise<boolean> {
  const refresh = getRefreshToken();
  if (!refresh) {
    return false;
  }
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), REFRESH_TIMEOUT_MS);
  try {
    const res = await fetch(`${getApiBaseUrl()}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
      signal: timeout.signal,
    });
    clearTimeout(timer);
    if (res.status === 401 || res.status === 403) {
      clearTokens();
      return false;
    }
    if (!res.ok) {
      return false;
    }
    let payload: unknown;
    try {
      payload = await res.json();
    } catch {
      return false;
    }
    if (!isTokenResponse(payload)) {
      return false;
    }
    setTokens(payload);
    return true;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

function waitForRefresh(pending: Promise<boolean>, signal?: AbortSignal): Promise<boolean> {
  if (signal === undefined) {
    return pending;
  }
  if (signal.aborted) {
    void pending.catch(() => undefined);
    return Promise.reject(abortError());
  }
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      reject(abortError());
    };
    signal.addEventListener("abort", onAbort, { once: true });
    pending.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", onAbort);
        reject(error);
      },
    );
  });
}

function refreshAccessToken(signal?: AbortSignal): Promise<boolean> {
  if (refreshPromise === null) {
    if (signal?.aborted) {
      return Promise.reject(abortError());
    }
    refreshPromise = performRefresh().finally(() => {
      refreshPromise = null;
    });
  }
  return waitForRefresh(refreshPromise, signal);
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, auth = true, skipRefresh = false, signal, headers: extraHeaders } =
    options;
  const headers: Record<string, string> = { ...extraHeaders };
  const formData = isFormDataBody(body);
  if (body !== undefined && !formData) {
    headers["Content-Type"] = "application/json";
  }
  if (auth) {
    const token = getAccessToken();
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
  }

  const res = await fetch(`${getApiBaseUrl()}/api/v1${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : formData ? body : JSON.stringify(body),
    signal,
  });

  if (res.status === 401 && auth && !skipRefresh) {
    const ok = await refreshAccessToken(signal);
    if (ok) {
      return apiRequest<T>(path, { ...options, skipRefresh: true });
    }
  }

  if (res.status === 204) {
    return undefined as T;
  }

  const text = await res.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text) as unknown;
    } catch {
      parsed = text;
    }
  }

  if (!res.ok) {
    const detail =
      typeof parsed === "object" &&
      parsed !== null &&
      "detail" in parsed &&
      typeof (parsed as { detail: unknown }).detail === "string"
        ? (parsed as { detail: string }).detail
        : `Request failed (${res.status})`;
    throw new ApiError(detail, res.status, parsed);
  }

  return parsed as T;
}

export async function apiStream(
  path: string,
  options: {
    signal?: AbortSignal;
    lastEventId?: string;
    skipRefresh?: boolean;
    method?: "GET" | "POST";
    body?: unknown;
  } = {},
): Promise<Response> {
  const headers: Record<string, string> = { Accept: "text/event-stream" };
  const token = getAccessToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  if (options.lastEventId) {
    headers["Last-Event-ID"] = options.lastEventId;
  }
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(`${getApiBaseUrl()}/api/v1${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
  });
  if (res.status === 401 && !options.skipRefresh) {
    const ok = await refreshAccessToken(options.signal);
    if (ok) {
      return apiStream(path, { ...options, skipRefresh: true });
    }
  }
  if (!res.ok) {
    const text = await res.text();
    let parsed: unknown = null;
    if (text) {
      try {
        parsed = JSON.parse(text) as unknown;
      } catch {
        parsed = text;
      }
    }
    const detail =
      typeof parsed === "object" &&
      parsed !== null &&
      "detail" in parsed &&
      typeof (parsed as { detail: unknown }).detail === "string"
        ? (parsed as { detail: string }).detail
        : `Request failed (${res.status})`;
    throw new ApiError(detail, res.status, parsed);
  }
  return res;
}
