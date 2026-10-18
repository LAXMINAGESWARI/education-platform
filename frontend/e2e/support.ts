import fs from "node:fs";
import path from "node:path";

import { expect, type Locator, type Page, type TestInfo } from "@playwright/test";

import { DEMO_PASSWORD } from "../src/auth/demoAccounts";

export const SHOT_DIR = path.resolve(import.meta.dirname, "artifacts");

export type Check = {
  id: string;
  viewport: string;
  status: "pass" | "fail" | "blocked";
  note: string;
};

export function record(testInfo: TestInfo, id: string, status: Check["status"], note: string) {
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const file = path.join(SHOT_DIR, "usecase-checks.json");
  const existing: Check[] = fs.existsSync(file)
    ? (JSON.parse(fs.readFileSync(file, "utf8")) as Check[])
    : [];
  existing.push({ id, viewport: testInfo.project.name, status, note });
  fs.writeFileSync(file, JSON.stringify(existing, null, 2));
}

export async function shot(page: Page, testInfo: TestInfo, name: string) {
  fs.mkdirSync(SHOT_DIR, { recursive: true });
  const file = path.join(SHOT_DIR, `${testInfo.project.name}-${name}.png`);
  await page.screenshot({ path: file });
  await testInfo.attach(name, { path: file, contentType: "image/png" });
}

/** Page-level horizontal scroll, including the shell scroller. Wide tables may
 * scroll inside their own container. */
export async function assertNoHorizontalOverflow(page: Page) {
  const result = await page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const scrolling = document.querySelector(".app__content");
    const contentExtra = scrolling ? scrolling.scrollWidth - scrolling.clientWidth : 0;
    const pageExtra = document.documentElement.scrollWidth - document.documentElement.clientWidth;
    const limit = scrolling?.clientWidth ?? vw;
    const wide: string[] = [];
    if (scrolling) {
      const hostRight = scrolling.getBoundingClientRect().right;
      for (const el of scrolling.querySelectorAll("*")) {
        const extra = el.scrollWidth - limit;
        const style = getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        const past = rect.right + (parseFloat(style.marginRight) || 0) - hostRight;
        if (extra > 1 || past > 1) {
          const label = el.className?.toString?.() || el.tagName;
          wide.push(
            `${label.toString().slice(0, 70)} sw+${Math.round(extra)} past+${Math.round(past)} ml${style.marginLeft} mr${style.marginRight}`,
          );
          if (wide.length >= 8) break;
        }
      }
    }
    const offenders: string[] = [];
    const nodes = document.body.querySelectorAll("*");
    for (const el of nodes) {
      const rect = el.getBoundingClientRect();
      if (rect.width < 4 || rect.height < 4) continue;
      if (rect.right <= vw + 1) continue;
      let parent: HTMLElement | null = el.parentElement;
      let contained = false;
      while (parent) {
        const style = getComputedStyle(parent);
        const overflow = `${style.overflowX} ${style.overflow}`;
        const clips = /(auto|scroll|hidden|clip)/.test(overflow);
        const parentRight = parent.getBoundingClientRect().right;
        if (clips && parentRight <= vw + 1) {
          contained = true;
          break;
        }
        parent = parent.parentElement;
      }
      if (!contained) {
        const label = el.className?.toString?.() || el.tagName;
        offenders.push(`${label.slice(0, 80)} +${Math.round(rect.right - vw)}px`);
        if (offenders.length >= 6) break;
      }
    }
    const main = document.querySelector("main");
    const innerWide: string[] = [];
    if (main) {
      for (const el of main.querySelectorAll("*")) {
        const extra = el.scrollWidth - main.clientWidth;
        if (extra > 1) {
          const label = el.className?.toString?.() || el.tagName;
          innerWide.push(`${label.toString().slice(0, 70)} +${Math.round(extra)}`);
          if (innerWide.length >= 6) break;
        }
      }
    }
    return {
      contentExtra,
      pageExtra,
      offenders,
      wide,
      innerWide,
      inner: window.innerWidth,
      appClient: scrolling?.clientWidth ?? null,
      appScroll: scrolling?.scrollWidth ?? null,
      appOffset: (scrolling as HTMLElement | null)?.offsetWidth ?? null,
      mainClient: main?.clientWidth ?? null,
      mainScroll: main?.scrollWidth ?? null,
      mainOffset: main?.offsetWidth ?? null,
    };
  });
  expect(result.contentExtra, JSON.stringify(result)).toBeLessThanOrEqual(1);
  expect(result.pageExtra, JSON.stringify(result)).toBeLessThanOrEqual(1);
  expect(result.offenders, result.offenders.join("\n")).toEqual([]);
}

export async function signIn(page: Page, email: string, password = DEMO_PASSWORD) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 20_000 });
}

export async function signOut(page: Page) {
  const direct = page.getByRole("button", { name: "Sign out" });
  if (await direct.isVisible()) {
    await direct.click();
  } else {
    await page.getByRole("button", { name: "Account menu" }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
  }
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
}

export async function openMenuIfPhone(page: Page) {
  const menu = page.getByRole("button", { name: "Open menu" });
  if (await menu.isVisible()) await menu.click();
}

export async function visibleControlHeight(locator: Locator): Promise<number> {
  const box = await locator.boundingBox();
  return box?.height ?? 0;
}
