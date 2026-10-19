/// <reference lib="webworker" />

import { CacheableResponsePlugin } from "workbox-cacheable-response";
import { clientsClaim } from "workbox-core";
import { ExpirationPlugin } from "workbox-expiration";
import { cleanupOutdatedCaches, createHandlerBoundToURL, matchPrecache, precacheAndRoute } from "workbox-precaching";
import { NavigationRoute, registerRoute, setCatchHandler } from "workbox-routing";
import { CacheFirst, NetworkOnly, StaleWhileRevalidate } from "workbox-strategies";

import { mustUseNetworkOnly, NAVIGATION_DENYLIST } from "./pwa/cachePolicy";

declare global {
  interface ImportMeta {
    readonly env: {
      readonly VITE_API_BASE_URL?: string;
    };
  }
}


const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

const networkOnly = new NetworkOnly({
  plugins: [
    {
      requestWillFetch: async ({ request }) => {
        if (request.mode === "navigate") return request;
        try {
          return new Request(request, { cache: "no-store" });
        } catch {
          return request;
        }
      },
    },
  ],
});

function isSensitive({ url, request }: { url: URL; request: Request }): boolean {
  return mustUseNetworkOnly(url, request.headers.get("Authorization"), self.location.origin, API_BASE);
}

const METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"] as const;

clientsClaim();
// Casts erase in the bundle, leaving the self.__WB_MANIFEST injection point.
precacheAndRoute((self as unknown as ServiceWorkerGlobalScope).__WB_MANIFEST);
cleanupOutdatedCaches();

self.addEventListener("message", (event: Event) => {
  const data: unknown = (event as ExtendableMessageEvent).data;
  const skip =
    data === "SKIP_WAITING" ||
    (typeof data === "object" && data !== null && "type" in data && data.type === "SKIP_WAITING");
  if (skip) void (self as unknown as ServiceWorkerGlobalScope).skipWaiting();
});

for (const method of METHODS) {
  registerRoute(isSensitive, networkOnly, method);
}

registerRoute(
  ({ request, url, sameOrigin }) =>
    sameOrigin &&
    (request.destination === "image" || request.destination === "font") &&
    !isSensitive({ url, request }),
  new CacheFirst({
    cacheName: "curioed-static",
    plugins: [
      new CacheableResponsePlugin({ statuses: [200] }),
      new ExpirationPlugin({ maxEntries: 80, maxAgeSeconds: 60 * 60 * 24 * 30 }),
    ],
  }),
);

registerRoute(
  ({ url }) => url.origin === "https://fonts.googleapis.com" || url.origin === "https://fonts.gstatic.com",
  new StaleWhileRevalidate({
    cacheName: "curioed-fonts",
    plugins: [
      new CacheableResponsePlugin({ statuses: [0, 200] }),
      new ExpirationPlugin({ maxEntries: 30, maxAgeSeconds: 60 * 60 * 24 * 365 }),
    ],
  }),
);

registerRoute(new NavigationRoute(createHandlerBoundToURL("/index.html"), { denylist: [...NAVIGATION_DENYLIST] }));

setCatchHandler(async ({ request }) => {
  if (request.destination === "document") {
    const offline = await matchPrecache("/offline.html");
    if (offline) return offline;
  }
  return Response.error();
});
