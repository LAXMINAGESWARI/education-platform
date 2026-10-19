import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const distDir = fileURLToPath(new URL("../dist/", import.meta.url));
const artifacts = "/opt/cursor/artifacts";

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".webmanifest": "application/manifest+json",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".json": "application/json",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
};

function cacheControl(pathname) {
  if (pathname === "/sw.js" || pathname === "/manifest.webmanifest" || pathname === "/index.html") {
    return "no-cache, max-age=0, must-revalidate";
  }
  if (pathname.startsWith("/assets/")) return "public, max-age=31536000, immutable";
  return "no-cache, max-age=0, must-revalidate";
}

function isApiPath(pathname) {
  return (
    pathname === "/api" ||
    pathname.startsWith("/api/") ||
    pathname === "/auth" ||
    pathname.startsWith("/auth/") ||
    pathname === "/health" ||
    pathname.startsWith("/health/") ||
    pathname === "/docs" ||
    pathname.startsWith("/docs/") ||
    pathname === "/redoc" ||
    pathname.startsWith("/redoc/") ||
    pathname === "/openapi.json"
  );
}

let apiHits = 0;
let dropApi = false;

const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", "http://127.0.0.1");
  const pathname = decodeURIComponent(url.pathname);

  if (pathname === "/__test/drop-api") {
    dropApi = true;
    res.end("ok");
    return;
  }

  if (isApiPath(pathname)) {
    if (dropApi) {
      req.socket.destroy();
      return;
    }
    res.setHeader("Content-Type", "application/json");
    res.setHeader("Cache-Control", "no-store");
    if (pathname === "/api/v1/ping") {
      apiHits += 1;
      res.end(JSON.stringify({ n: apiHits }));
      return;
    }
    res.statusCode = 401;
    res.end(JSON.stringify({ detail: "unauthorized" }));
    return;
  }

  const relative = normalize(pathname).replace(/^(\.\.[/\\])+/, "");
  const filePath = join(distDir, relative === "/" ? "index.html" : relative);
  if (!filePath.startsWith(distDir)) {
    res.statusCode = 400;
    res.end("bad path");
    return;
  }

  try {
    const body = await readFile(filePath);
    const type = MIME[extname(filePath)] ?? "application/octet-stream";
    res.setHeader("Content-Type", type);
    res.setHeader("Cache-Control", cacheControl(pathname));
    res.end(body);
  } catch {
    const body = await readFile(join(distDir, "index.html"));
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    res.setHeader("Cache-Control", "no-cache, max-age=0, must-revalidate");
    res.end(body);
  }
});

await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const { port } = server.address();
const base = `http://127.0.0.1:${port}`;
const report = { base, checks: [] };

function check(name, ok, detail) {
  report.checks.push({ name, ok, detail });
  if (!ok) console.error("FAIL", name, detail ?? "");
  else console.log("ok", name);
}

const userData = fileURLToPath(new URL("../.pwa-chrome-profile/", import.meta.url));
const context = await chromium.launchPersistentContext(userData, {
  channel: "chrome",
  headless: true,
  viewport: { width: 390, height: 844 },
  isMobile: true,
  hasTouch: true,
  deviceScaleFactor: 2,
  userAgent:
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
});
try {
  const indexHtml = await (await fetch(`${base}/`)).text();
  const manifestLinks = indexHtml.match(/rel="manifest"/g) ?? [];
  check("one manifest link", manifestLinks.length === 1, String(manifestLinks.length));
  check("apple web app meta", indexHtml.includes("apple-mobile-web-app-capable"));

  const swHeaders = await fetch(`${base}/sw.js`);
  const manifestHeaders = await fetch(`${base}/manifest.webmanifest`);
  const manifest = await manifestHeaders.json();
  check(
    "sw.js content type",
    (swHeaders.headers.get("content-type") ?? "").includes("application/javascript"),
    swHeaders.headers.get("content-type"),
  );
  check(
    "sw.js is not long-cached",
    (swHeaders.headers.get("cache-control") ?? "").includes("no-cache") &&
      !(swHeaders.headers.get("cache-control") ?? "").includes("immutable"),
    swHeaders.headers.get("cache-control"),
  );
  check(
    "manifest content type",
    (manifestHeaders.headers.get("content-type") ?? "").includes("application/manifest+json"),
    manifestHeaders.headers.get("content-type"),
  );
  check("manifest name", manifest.name === "CurioEd" && manifest.short_name === "CurioEd");
  check("manifest display", manifest.display === "standalone" && manifest.start_url === "/" && manifest.scope === "/");
  check(
    "manifest icons",
    manifest.icons.some((icon) => icon.sizes === "192x192") &&
      manifest.icons.some((icon) => icon.sizes === "512x512" && icon.purpose === "any") &&
      manifest.icons.some((icon) => icon.sizes === "512x512" && icon.purpose === "maskable"),
  );
  check("manifest colours", manifest.theme_color === "#3f6f5a" && manifest.background_color === "#f4f6f3");

  const phone = {
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
    deviceScaleFactor: 2,
  };

  const page = context.pages()[0] ?? (await context.newPage());
  await page.goto(base, { waitUntil: "load" });
  await page.waitForFunction(async () => {
    const registration = await navigator.serviceWorker.getRegistration();
    return Boolean(registration?.active);
  });
  await page.reload({ waitUntil: "load" });
  await page.waitForFunction(() => navigator.serviceWorker.controller !== null);

  const pings = await page.evaluate(async () => {
    const first = await fetch("/api/v1/ping").then((res) => res.json());
    const second = await fetch("/api/v1/ping").then((res) => res.json());
    return { first, second };
  });
  check("api responses are not cached", pings.first.n === 1 && pings.second.n === 2, JSON.stringify(pings));

  const cached = await page.evaluate(async () => {
    const hits = [];
    for (const name of await caches.keys()) {
      const cache = await caches.open(name);
      for (const request of await cache.keys()) {
        if (/\/api\/|\/auth|\/health|\/openapi\.json/.test(request.url)) hits.push(request.url);
      }
    }
    return hits;
  });
  check("cache storage has no API responses", cached.length === 0, cached.join(", "));

  const session = await context.newCDPSession(page);
  let installability = await session.send("Page.getInstallabilityErrors");
  for (let attempt = 0; attempt < 5 && installability.installabilityErrors?.length; attempt += 1) {
    await page.waitForTimeout(500);
    installability = await session.send("Page.getInstallabilityErrors");
  }
  const appManifest = await session.send("Page.getAppManifest");
  check(
    "Chrome installability",
    (installability.installabilityErrors ?? []).length === 0,
    JSON.stringify(installability.installabilityErrors ?? []),
  );
  check("parsed manifest url", Boolean(appManifest.data) && !(appManifest.errors ?? []).length, JSON.stringify(appManifest.errors ?? []));

  const prompted = await page.evaluate(() => {
    const event = new Event("beforeinstallprompt", { cancelable: true });
    Object.assign(event, {
      prompt: () => Promise.resolve(),
      userChoice: Promise.resolve({ outcome: "dismissed", platform: "web" }),
    });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  });
  check("beforeinstallprompt is captured", prompted === true);
  await page.getByRole("button", { name: "Install app" }).waitFor();
  await page.screenshot({ path: `${artifacts}/pwa-install-prompt-phone.png` });

  await context.setOffline(true);
  await page.goto(`${base}/subjects/1`, { waitUntil: "domcontentloaded" });
  check("offline app shell", (await page.title()) === "CurioEd");
  await context.setOffline(false);
  await fetch(`${base}/__test/drop-api`, { method: "POST" });
  const offlineResponse = await page.goto(`${base}/api/v1/ping`, { waitUntil: "domcontentloaded" });
  const offlineText = await page.locator("body").innerText();
  check(
    "offline document fallback",
    offlineText.includes("You're offline") && (offlineResponse?.status() ?? 0) < 500,
    offlineText.slice(0, 120),
  );
  await page.screenshot({ path: `${artifacts}/pwa-offline-phone.png` });

  const extra = await chromium.launch({ channel: "chrome", headless: true });
  const ios = await extra.newContext({
    ...phone,
    userAgent:
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
  });
  const iosPage = await ios.newPage();
  await iosPage.goto(`${base}/login`, { waitUntil: "load" });
  await iosPage.getByRole("region", { name: "Install CurioEd" }).waitFor();
  await iosPage.screenshot({ path: `${artifacts}/pwa-ios-install-hint.png` });
  await ios.close();

  const standalone = await extra.newContext({
    ...phone,
    userAgent:
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
  });
  const standalonePage = await standalone.newPage();
  // Headless Chrome ignores display-mode emulation. iOS home-screen apps set navigator.standalone.
  await standalonePage.addInitScript(() => {
    Object.defineProperty(navigator, "standalone", { configurable: true, value: true });
  });
  await standalonePage.goto(`${base}/login`, { waitUntil: "load" });
  const standaloneMode = await standalonePage.evaluate(() => navigator.standalone === true);
  check("ios standalone flag", standaloneMode === true);
  check(
    "standalone hides the install hint",
    (await standalonePage.getByRole("region", { name: "Install CurioEd" }).count()) === 0,
  );
  await standalonePage.screenshot({ path: `${artifacts}/pwa-standalone-phone.png` });
  await standalone.close();
  await extra.close();
} finally {
  await context.close();
  await new Promise((resolve) => server.close(resolve));
}

const failed = report.checks.filter((item) => !item.ok);
await import("node:fs/promises").then(({ writeFile }) =>
  writeFile(`${artifacts}/pwa-validation.json`, JSON.stringify(report, null, 2)),
);
if (failed.length) {
  console.error(JSON.stringify(failed, null, 2));
  process.exit(1);
}
console.log("PWA validation passed");
