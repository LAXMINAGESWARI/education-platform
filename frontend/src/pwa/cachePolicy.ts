/**
 * What the service worker is allowed to store.
 * API, auth, health, docs, and any request carrying Authorization stay on the network.
 */

const SENSITIVE_PREFIXES = ["/api", "/auth", "/health", "/docs", "/redoc"] as const;

export const NAVIGATION_DENYLIST: readonly RegExp[] = [
  /^\/api(?:\/|$)/,
  /^\/auth(?:\/|$)/,
  /^\/health(?:\/|$)/,
  /^\/docs(?:\/|$)/,
  /^\/redoc(?:\/|$)/,
  /^\/openapi\.json$/,
];

export function isSensitivePath(pathname: string): boolean {
  if (pathname === "/openapi.json") return true;
  return SENSITIVE_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

/** True when `apiBase` is the origin (or a path prefix) this URL belongs to. */
export function isApiBaseUrl(url: URL, apiBase: string): boolean {
  const trimmed = apiBase.trim().replace(/\/$/, "");
  if (!trimmed) return false;
  let base: URL;
  try {
    base = new URL(trimmed);
  } catch {
    return false;
  }
  if (url.origin !== base.origin) return false;
  const basePath = base.pathname.replace(/\/$/, "");
  if (!basePath) return true;
  return url.pathname === basePath || url.pathname.startsWith(`${basePath}/`);
}

export function mustUseNetworkOnly(
  url: URL,
  authorization: string | null,
  pageOrigin: string,
  apiBase: string,
): boolean {
  if (authorization) return true;
  if (isApiBaseUrl(url, apiBase)) return true;
  return url.origin === pageOrigin && isSensitivePath(url.pathname);
}
