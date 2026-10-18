import { expect, test, type Locator } from "@playwright/test";

import { DEMO_ADMIN, DEMO_TEACHER } from "../src/auth/demoAccounts";
import { assertNoHorizontalOverflow, signIn, visibleControlHeight } from "./support";

function widthsFor(project: string): number[] {
  return project === "phone" ? [360, 375, 412] : [1280];
}

async function fitsInBox(locator: Locator) {
  return locator.evaluate((el) => {
    const thread = el.closest(".assistant-chat__thread, .policy-chat__thread");
    if (!thread) return false;
    const item = el.getBoundingClientRect();
    const box = thread.getBoundingClientRect();
    return item.top >= box.top - 1 && item.bottom <= box.bottom + 1 && el.scrollHeight <= el.clientHeight + 2;
  });
}

async function threadIsAtEnd(locator: Locator) {
  return locator.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight < 48);
}

test("teacher assistant keeps a scrolling thread and a compact composer", async ({ page }, testInfo) => {
  const phone = testInfo.project.name === "phone";
  await signIn(page, DEMO_TEACHER.email);

  for (const width of widthsFor(testInfo.project.name)) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/teacher/assistant");
    await expect(page.getByRole("heading", { name: "Assistant" })).toBeAttached();

    const select = page.getByLabel("Suggested questions");
    await expect(select).toBeVisible();
    const selectHeight = await visibleControlHeight(select);
    expect(selectHeight).toBeGreaterThanOrEqual(44);
    expect(selectHeight).toBeLessThanOrEqual(48);

    const intro = page.locator(".assistant-chat__empty");
    await expect(intro).toBeVisible();
    await expect(intro).toContainText("type your own");
    expect(await fitsInBox(intro)).toBe(true);

    const threadHeight = await page.locator(".assistant-chat__thread").evaluate((el) => el.clientHeight);
    expect(threadHeight).toBeGreaterThan(phone ? 180 : 320);
    expect(threadHeight).toBeGreaterThan(selectHeight);

    const ask = page.getByRole("button", { name: "Ask" });
    await expect(ask).toBeInViewport();
    const askBox = await ask.boundingBox();
    expect((askBox?.y ?? 0) + (askBox?.height ?? 0)).toBeGreaterThan(720);
    expect(await visibleControlHeight(ask)).toBeGreaterThanOrEqual(44);
    const fontSize = await page
      .getByRole("textbox", { name: "Question", exact: true })
      .evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
    expect(fontSize).toBeGreaterThanOrEqual(16);
    await assertNoHorizontalOverflow(page);
  }

  await page.setViewportSize({ width: phone ? 375 : 1280, height: 800 });
  await page.goto("/teacher/assistant");
  const question = "How many students do I teach in total?";
  await page.getByLabel("Suggested questions").selectOption(question);
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByLabel("You")).toContainText(question);
  await expect(page.getByLabel("Assistant").or(page.getByRole("alert"))).toBeVisible({ timeout: 45_000 });

  const unbroken = `Unbroken${"X".repeat(90)}`;
  await page.getByRole("textbox", { name: "Question", exact: true }).fill(unbroken);
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.locator(".assistant-chat__thread")).toContainText(unbroken);
  await expect.poll(() => threadIsAtEnd(page.locator(".assistant-chat__thread"))).toBe(true);
  await assertNoHorizontalOverflow(page);
  await expect(page.getByRole("button", { name: "Ask" })).toBeInViewport();
});

test("admin policy chat gives the thread the screen and pins the composer", async ({ page }, testInfo) => {
  const phone = testInfo.project.name === "phone";
  await signIn(page, DEMO_ADMIN.email);

  for (const width of widthsFor(testInfo.project.name)) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/admin/policy");
    await expect(page.getByRole("heading", { name: "Policy assistant" })).toBeAttached();
    await expect(page.getByLabel("Message")).toBeVisible();

    const threadHeight = await page.locator(".policy-chat__thread").evaluate((el) => el.clientHeight);
    const sidebarHeight = await page.locator(".policy-chat__sidebar").evaluate((el) => el.getBoundingClientRect().height);
    if (phone) {
      await expect(page.getByRole("button", { name: "Expand chats" })).toBeVisible();
      expect(sidebarHeight).toBeLessThan(96);
      expect(threadHeight).toBeGreaterThan(240);
      const ring = await page.locator(".policy-chat__context").boundingBox();
      const send = await page.getByRole("button", { name: "Send" }).boundingBox();
      expect(ring).not.toBeNull();
      expect(send).not.toBeNull();
      expect((ring?.y ?? 0) + (ring?.height ?? 0)).toBeLessThanOrEqual(send?.y ?? 0);
    } else {
      await expect(page.getByRole("button", { name: "Collapse chats" })).toBeVisible();
      await expect(page.locator(".policy-chat__conv").first()).toBeVisible();
      expect(threadHeight).toBeGreaterThan(320);
    }

    const send = page.getByRole("button", { name: "Send" });
    await expect(send).toBeInViewport();
    const sendBox = await send.boundingBox();
    expect((sendBox?.y ?? 0) + (sendBox?.height ?? 0)).toBeGreaterThan(720);
    expect(await visibleControlHeight(send)).toBeGreaterThanOrEqual(44);
    const fontSize = await page.getByLabel("Message").evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
    expect(fontSize).toBeGreaterThanOrEqual(16);
    await assertNoHorizontalOverflow(page);
  }

  await page.setViewportSize({ width: phone ? 375 : 1280, height: 800 });
  await page.goto("/admin/policy");
  const message = `PolicyCheck${"Y".repeat(80)}`;
  await page.getByLabel("Message").fill(message);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator(".policy-chat__thread")).toContainText(message);
  await expect.poll(() => threadIsAtEnd(page.locator(".policy-chat__thread"))).toBe(true);
  await expect(page.getByRole("button", { name: "Send" })).toBeInViewport();
  await assertNoHorizontalOverflow(page);
});
