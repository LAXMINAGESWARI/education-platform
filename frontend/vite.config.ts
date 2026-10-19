import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";
import { defineConfig } from "vitest/config";

const frontendRoot = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.ts",
      registerType: "prompt",
      injectRegister: false,
      includeAssets: [
        "favicon.png",
        "apple-touch-icon.png",
        "offline.html",
        "icons/icon-192.png",
        "icons/icon-512.png",
        "icons/icon-512-maskable.png",
      ],
      manifest: {
        name: "CurioEd",
        short_name: "CurioEd",
        description: "Lessons, quizzes, and class tools for CurioEd.",
        id: "/",
        start_url: "/",
        scope: "/",
        display: "standalone",
        // Portrait is the phone layout. "any" keeps landscape tablets usable.
        orientation: "any",
        background_color: "#f4f6f3",
        theme_color: "#3f6f5a",
        lang: "en",
        categories: ["education"],
        icons: [
          {
            src: "/icons/icon-192.png",
            sizes: "192x192",
            type: "image/png",
            purpose: "any",
          },
          {
            src: "/icons/icon-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "any",
          },
          {
            src: "/icons/icon-512-maskable.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ],
      },
      injectManifest: {
        globPatterns: ["**/*.{js,css,html,png,svg,ico,webmanifest,woff,woff2}"],
        globIgnores: ["**/*.map"],
        maximumFileSizeToCacheInBytes: 5 * 1024 * 1024,
      },
      devOptions: {
        enabled: false,
      },
    }),
  ],
  server: {
    port: 5173,
    fs: {
      allow: [resolve(frontendRoot, "..")],
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
});
