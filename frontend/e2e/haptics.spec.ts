import { expect, test } from "@playwright/test";

import { DEMO_STUDENT } from "../src/auth/demoAccounts";
import { openMenuIfPhone, signIn, visibleControlHeight } from "./support";

test("haptic feedback toggle is on the phone drawer and persists", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "phone", "Phone viewport only");

  await page.goto("/login");
  await page.evaluate(() => localStorage.removeItem("ep.haptics"));
  await signIn(page, DEMO_STUDENT.email);
  await expect(page.getByRole("heading", { name: "Your subjects", level: 1 })).toBeAttached();

  await openMenuIfPhone(page);
  const toggle = page.getByRole("switch", { name: "Haptic feedback" });
  await expect(toggle).toBeVisible();
  expect(await visibleControlHeight(page.locator("label.haptic-toggle"))).toBeGreaterThanOrEqual(44);

  await toggle.setChecked(false);
  await expect(toggle).not.toBeChecked();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Your subjects", level: 1 })).toBeAttached();
  await openMenuIfPhone(page);
  await expect(page.getByRole("switch", { name: "Haptic feedback" })).not.toBeChecked();

  await page.screenshot({ path: "/opt/cursor/artifacts/haptic-toggle-phone.png" });

  await page.getByRole("switch", { name: "Haptic feedback" }).setChecked(true);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Your subjects", level: 1 })).toBeAttached();
  await openMenuIfPhone(page);
  await expect(page.getByRole("switch", { name: "Haptic feedback" })).toBeChecked();
});
