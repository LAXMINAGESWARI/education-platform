import caddy from "../../../deploy/Caddyfile.site?raw";
import nginx from "../../../deploy/nginx.conf?raw";
import html from "../../index.html?raw";

import { describe, expect, it } from "vitest";

describe("frontend hosting headers", () => {
  it("serves the service worker and manifest with the right type and no long cache", () => {
    expect(nginx).toMatch(/location = \/sw\.js[\s\S]*application\/javascript/);
    expect(nginx).toMatch(/location = \/sw\.js[\s\S]*no-cache, max-age=0, must-revalidate/);
    expect(nginx).not.toMatch(/location = \/sw\.js[\s\S]*no-store/);
    expect(nginx).toMatch(/location = \/manifest\.webmanifest[\s\S]*application\/manifest\+json/);
    expect(nginx).toMatch(/location = \/manifest\.webmanifest[\s\S]*no-cache, max-age=0, must-revalidate/);
    expect(nginx).toMatch(/location \/assets\/[\s\S]*max-age=31536000, immutable/);

    expect(caddy).toMatch(/handle \/sw\.js[\s\S]*application\/javascript/);
    expect(caddy).toMatch(/handle \/sw\.js[\s\S]*no-cache, max-age=0, must-revalidate/);
    expect(caddy).toMatch(/handle \/manifest\.webmanifest[\s\S]*application\/manifest\+json/);
  });

  it("advertises the Apple web app tags", () => {
    expect(html).toContain('name="theme-color" content="#3f6f5a"');
    expect(html).toContain('name="apple-mobile-web-app-capable" content="yes"');
    expect(html).toContain('name="apple-mobile-web-app-status-bar-style" content="black-translucent"');
    expect(html).toContain('rel="apple-touch-icon"');
    expect(html).toContain("viewport-fit=cover");
  });
});
