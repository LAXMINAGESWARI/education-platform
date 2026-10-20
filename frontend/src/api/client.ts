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

function abortError(): DOMException {
  return new DOMException("Aborted", "AbortError");
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

async function performRefresh(signal?: AbortSignal): Promise<boolean> {
  if (signal?.aborted) {
    throw abortError();
  }
  const refresh = getRefreshToken();
  if (!refresh) {
    return false;
  }
  try {
    const res = await fetch(`${getApiBaseUrl()}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
      signal,
    });
    if (signal?.aborted) {
      throw abortError();
    }
    if (!res.ok) {
      clearTokens();
      return false;
    }
    const data = (await res.json()) as TokenResponse;
    if (signal?.aborted) {
      throw abortError();
    }
    setTokens(data);
    return true;
  } catch (error) {
    if (isAbortError(error) || signal?.aborted) {
      throw abortError();
    }
    clearTokens();
    return false;
  }
}

function waitForRefresh(pending: Promise<boolean>, signal?: AbortSignal): Promise<boolean> {
  if (signal === undefined) {
    return pending;
  }
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      reject(abortError());
    };
    if (signal.aborted) {
      onAbort();
      return;
    }
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
  if (signal?.aborted) {
    return Promise.reject(abortError());
  }
  if (refreshPromise === null) {
    refreshPromise = performRefresh(signal).finally(() => {
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
  options: { signal?: AbortSignal; lastEventId?: string; skipRefresh?: boolean } = {},
): Promise<Response> {
  const headers: Record<string, string> = { Accept: "text/event-stream" };
  const token = getAccessToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  if (options.lastEventId) {
    headers["Last-Event-ID"] = options.lastEventId;
  }
  const res = await fetch(`${getApiBaseUrl()}/api/v1${path}`, {
    method: "GET",
    headers,
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
