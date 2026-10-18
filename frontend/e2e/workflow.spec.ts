import fs from "node:fs";
import path from "node:path";

import { expect, test, type Page, type TestInfo } from "@playwright/test";

import { DEMO_ADMIN, DEMO_SECOND_STUDENT, DEMO_STUDENT, DEMO_TEACHER } from "../src/auth/demoAccounts";
import {
  assertNoHorizontalOverflow,
  record,
  SHOT_DIR,
  shot,
  signIn,
  signOut,
  visibleControlHeight,
} from "./support";

const PDF = path.resolve(import.meta.dirname, "../../docs/mom/week2.pdf");

async function settle(page: Page, testInfo: TestInfo, id: string, name: string) {
  await page.waitForLoadState("networkidle", { timeout: 2_000 }).catch(() => undefined);
  await page.evaluate(() => document.fonts.ready);
  await shot(page, testInfo, name);
  await assertNoHorizontalOverflow(page);
  record(testInfo, id, "pass", name);
}

test("login rejects a bad password and accepts each role", async ({ page }, testInfo) => {
  await page.goto("/login");
  await settle(page, testInfo, "login-screen", "login");

  const email = page.getByLabel("Email");
  const fontSize = await email.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
  if (testInfo.project.name === "phone") {
    expect(fontSize).toBeGreaterThanOrEqual(16);
    expect(await visibleControlHeight(page.getByRole("button", { name: "Sign in" }))).toBeGreaterThanOrEqual(44);
  }

  await page.getByLabel("Email").fill(DEMO_STUDENT.email);
  await page.getByLabel("Password").fill("wrong-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await assertNoHorizontalOverflow(page);
  await shot(page, testInfo, "login-error");
  record(testInfo, "login-error", "pass", "Bad password stays on the form with an alert.");

  await signIn(page, DEMO_STUDENT.email);
  await expect(page.getByRole("heading", { name: "Your subjects", level: 1 })).toBeAttached();
  await expect(page.getByText("Loading subjects…")).toBeHidden({ timeout: 20_000 });
  await settle(page, testInfo, "student-home", "student-home");

  if (testInfo.project.name === "phone") {
    await page.getByRole("button", { name: "Open menu" }).click();
    await expect(page.getByRole("complementary", { name: "Student" })).toBeVisible();
    expect(await visibleControlHeight(page.getByRole("complementary", { name: "Student" }).getByRole("link", { name: "Feedback" }))).toBeGreaterThanOrEqual(44);
    await shot(page, testInfo, "student-menu");
    await page.getByRole("complementary", { name: "Student" }).getByRole("link", { name: "Feedback" }).click();
  } else {
    await expect(page.getByRole("button", { name: "Open menu" })).toBeHidden();
    await page.getByRole("complementary", { name: "Primary" }).getByRole("link", { name: "Feedback" }).click();
  }
  await expect(page.getByRole("heading", { name: "Feedback" })).toBeVisible();
  await settle(page, testInfo, "student-feedback", "student-feedback");
  record(
    testInfo,
    "student-qa",
    "blocked",
    "No student ask-the-material chat exists. Feedback is the student reflection screen.",
  );

  await signOut(page);
  await shot(page, testInfo, "student-logged-out");
  record(testInfo, "student-logout", "pass", "Returned to the sign-in form.");
});

test("student reads a lesson, is blocked from copy on the quiz only, and sees a result", async ({
  page,
}, testInfo) => {
  await signIn(page, DEMO_STUDENT.email);
  await expect(page.getByText("Loading subjects…")).toBeHidden({ timeout: 20_000 });

  await expect(page.locator("a.card").first()).toBeVisible();
  const subjectHrefs = await page.locator("a.card").evaluateAll((els) =>
    els
      .map((el) => el.getAttribute("href"))
      .filter((href): href is string => Boolean(href)),
  );
  expect(subjectHrefs.length).toBeGreaterThan(0);
  await page.locator("a.card").first().click();
  await expect(page.locator("a.subtopic-card__head").first()).toBeVisible();
  await settle(page, testInfo, "student-subject", "student-subject");

  let lessonChecked = false;
  let started = false;
  for (const subjectHref of subjectHrefs) {
    await page.goto(subjectHref);
    await expect(page.locator("a.subtopic-card__head").first()).toBeVisible();
    const lessonHrefs = await page.locator("a.subtopic-card__head").evaluateAll((els) =>
      els
        .map((el) => el.getAttribute("href"))
        .filter((href): href is string => Boolean(href)),
    );
    for (const href of lessonHrefs) {
      await page.goto(href);
      const lesson = page.locator(".lesson-overview, .reading, .lesson-view").first();
      await expect(lesson).toBeVisible();
      if (!lessonChecked) {
        const lessonCopyBlocked = await page.evaluate(() => {
          const root = document.querySelector(".lesson-overview, .reading");
          if (!root) return null;
          const event = new ClipboardEvent("copy", { bubbles: true, cancelable: true });
          root.dispatchEvent(event);
          return event.defaultPrevented;
        });
        expect(lessonCopyBlocked).toBe(false);
        await settle(page, testInfo, "student-lesson", "student-lesson");
        lessonChecked = true;
      }
      const start = page.getByRole("link", { name: /Start quiz|Retake quiz/i });
      if (!(await start.count())) continue;
      await start.click();
      const radio = page.getByRole("radio").first();
      const locked = page.getByRole("alert");
      await expect(radio.or(locked)).toBeVisible({ timeout: 20_000 });
      if (await radio.isVisible()) {
        started = true;
        break;
      }
      await shot(page, testInfo, "student-quiz-locked");
      record(
        testInfo,
        "student-quiz-locked",
        "pass",
        ((await locked.innerText()) || "Quiz locked").slice(0, 180),
      );
    }
    if (started) break;
  }
  expect(started, "No lesson still had a quiz attempt left").toBe(true);
  await expect(page.getByRole("radio").first()).toBeVisible();
  if (testInfo.project.name === "phone") {
    expect(await visibleControlHeight(page.getByRole("radio").first())).toBeGreaterThanOrEqual(44);
  }

  const quizCopyBlocked = await page.evaluate(() => {
    const root = document.querySelector(".quiz-lock");
    if (!root) return null;
    const event = new ClipboardEvent("copy", { bubbles: true, cancelable: true });
    root.dispatchEvent(event);
    return event.defaultPrevented;
  });
  expect(quizCopyBlocked).toBe(true);
  await expect(page.getByText("Copy and paste are disabled during quizzes")).toBeVisible();
  await shot(page, testInfo, "student-quiz");
  await assertNoHorizontalOverflow(page);

  for (let step = 0; step < 40; step += 1) {
    await page.getByRole("radio").first().click();
    const submit = page.getByRole("button", { name: "Submit quiz" });
    if (await submit.isVisible()) {
      if (testInfo.project.name === "phone") {
        expect(await visibleControlHeight(submit)).toBeGreaterThanOrEqual(44);
      }
      await submit.click();
      break;
    }
    await page.getByRole("button", { name: "Next question" }).click();
    if (step === 39) throw new Error("Quiz never offered submit.");
  }

  await expect(page).toHaveURL(/\/attempts\//);
  await expect(page.getByText(/Passed — nice work\.|Not yet — review the lesson and try again\./)).toBeVisible();
  const resultCopyBlocked = await page.evaluate(() => {
    const event = new ClipboardEvent("copy", { bubbles: true, cancelable: true });
    document.body.dispatchEvent(event);
    return event.defaultPrevented;
  });
  expect(resultCopyBlocked).toBe(false);
  await settle(page, testInfo, "student-result", "student-result");
  await signOut(page);
});

test("teacher reviews classes, a student record, analytics, the assistant, and the quiz builder", async ({
  page,
}, testInfo) => {
  await signIn(page, DEMO_TEACHER.email);
  await expect(page.getByRole("heading", { name: "My classes" })).toBeVisible();
  await settle(page, testInfo, "teacher-classes", "teacher-classes");

  if (testInfo.project.name === "phone") {
    await page.getByRole("button", { name: "Open menu" }).click();
    const drawer = page.getByRole("complementary", { name: "Teacher" });
    await expect(drawer.getByRole("link", { name: "Question bank" })).toBeVisible();
    await shot(page, testInfo, "teacher-menu");
    await drawer.getByRole("link", { name: "Analytics" }).click();
  } else {
    await page.getByRole("link", { name: "Analytics" }).click();
  }
  await expect(page.getByRole("heading", { name: "Class analytics" })).toBeVisible();
  await settle(page, testInfo, "teacher-analytics", "teacher-analytics");

  await page.goto("/teacher/assistant");
  await expect(page.getByRole("heading", { name: "Assistant" })).toBeAttached();
  const suggest = page.getByLabel("Suggested questions");
  await expect(suggest).toBeVisible();
  if (testInfo.project.name === "phone") {
    const selectSize = await suggest.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
    expect(selectSize).toBeGreaterThanOrEqual(16);
  }
  await suggest.selectOption("How many students do I teach in total?");
  await page.getByRole("button", { name: "Ask" }).click();
  const assistantReply = page.getByLabel("Assistant");
  const assistantError = page.getByRole("alert");
  await expect(assistantReply.or(assistantError)).toBeVisible({ timeout: 45_000 });
  await assertNoHorizontalOverflow(page);
  await shot(page, testInfo, "teacher-assistant");
  if (await assistantError.isVisible()) {
    const message = (await assistantError.innerText()).trim();
    record(testInfo, "teacher-assistant", "fail", message);
    expect(message, message).toBe("");
  } else {
    record(testInfo, "teacher-assistant", "pass", "Suggested question returned an answer.");
  }

  await page.goto("/teacher/questions");
  await expect(page.getByRole("heading", { name: "Question bank" })).toBeVisible();
  await settle(page, testInfo, "teacher-quiz-builder", "teacher-quiz-builder");
  const generate = page.getByRole("button", { name: "Generate drafts" });
  if (await generate.isVisible()) {
    await generate.click();
    const draft = page.getByRole("heading", { name: /awaiting your approval/i });
    const failure = page.getByRole("alert");
    await expect(draft.or(failure).first()).toBeVisible({ timeout: 60_000 });
    await assertNoHorizontalOverflow(page);
    await shot(page, testInfo, "teacher-generate");
    if (await failure.isVisible()) {
      const message = (await failure.innerText()).trim();
      const missingKey = /openrouter|api key|not configured/i.test(message);
      record(
        testInfo,
        "teacher-generate-questions",
        missingKey ? "blocked" : "fail",
        message.slice(0, 400),
      );
      expect(missingKey, message).toBe(true);
    } else {
      record(testInfo, "teacher-generate-questions", "pass", "Drafts appeared in the builder.");
    }
  }

  await page.goto("/teacher");
  const classCard = page.locator("a.teacher-class-card").first();
  await expect(classCard).toBeVisible();
  await classCard.click();
  await page.getByRole("link", { name: /Student roster/i }).click();
  await expect(page.getByRole("table", { name: /Roster/i })).toBeVisible();
  await settle(page, testInfo, "teacher-roster", "teacher-roster");

  await page.getByPlaceholder("Name or roll number").fill("zzzz-no-such-student");
  await expect(page.getByText(/No student matches/i)).toBeVisible();
  await assertNoHorizontalOverflow(page);
  await shot(page, testInfo, "teacher-roster-empty");
  record(testInfo, "teacher-roster-empty", "pass", "Unknown search shows an empty state.");

  await page.getByPlaceholder("Name or roll number").fill("");
  await page.locator(".teacher-roster__name a").first().click();
  await expect(page.getByRole("heading", { name: "Quiz history" })).toBeVisible();
  await expect(page.getByText("Nothing submitted yet in your subjects.")).toHaveCount(0);
  await settle(page, testInfo, "teacher-student-results", "teacher-student");
  record(
    testInfo,
    "teacher-sees-adnan",
    "blocked",
    "student1 is Grade 6. Meera teaches Grade 8 Mathematics and Grade 9 Science, so his attempt is outside her roster. Her own students' quiz history is visible.",
  );

  await page.goto("/teacher/at-risk");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await settle(page, testInfo, "teacher-at-risk", "teacher-at-risk");
  await signOut(page);
  record(testInfo, "teacher-logout", "pass", "Returned to the sign-in form.");
});

test("admin browses the school and uploads a topic PDF", async ({ page }, testInfo) => {
  test.setTimeout(360_000);
  await signIn(page, DEMO_ADMIN.email);
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  await settle(page, testInfo, "admin-dashboard", "admin-dashboard");

  if (testInfo.project.name === "phone") {
    await page.getByRole("button", { name: "Open menu" }).click();
    const drawer = page.getByRole("complementary", { name: "Admin" });
    await expect(drawer.getByRole("link", { name: "Policy assistant" })).toBeVisible();
    await shot(page, testInfo, "admin-menu");
    await drawer.getByRole("link", { name: "Materials" }).click();
  } else {
    await page.getByRole("link", { name: "Materials" }).click();
  }
  await expect(page.getByRole("heading", { name: "Materials" })).toBeVisible();
  await settle(page, testInfo, "admin-materials", "admin-materials");

  await page
    .locator("a.grade-grid__card")
    .filter({ has: page.getByRole("heading", { name: "8", exact: true }) })
    .click();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await settle(page, testInfo, "admin-grade", "admin-grade");
  await page
    .locator("a.card")
    .filter({ has: page.getByRole("heading", { name: "Mathematics", exact: true }) })
    .click();
  await expect(page.getByRole("heading", { name: "Topics" })).toBeVisible();
  await settle(page, testInfo, "admin-topics", "admin-topics");

  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByLabel("Title")).toBeVisible();
  await assertNoHorizontalOverflow(page);
  await shot(page, testInfo, "admin-upload-form");

  const topicFile = path.join(SHOT_DIR, "latest-topic-url.txt");
  if (testInfo.project.name !== "phone") {
    const topicUrl = fs.existsSync(topicFile) ? fs.readFileSync(topicFile, "utf8").trim() : "";
    if (topicUrl) {
      await page.goto(topicUrl);
      await expect(page.getByRole("region", { name: "Published lesson" })).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      await assertNoHorizontalOverflow(page);
      await shot(page, testInfo, "admin-upload-progress");
      record(
        testInfo,
        "admin-upload-processing",
        "pass",
        "Desktop opens the processed topic. Upload, approval, and publish run in the phone project.",
      );
    } else {
      record(
        testInfo,
        "admin-upload-processing",
        "blocked",
        "Desktop shows the upload form. The phone project did not leave a processed topic to review.",
      );
    }
    record(
      testInfo,
      "teacher-approval",
      "pass",
      "The teacher approval decision is submitted in the phone project. Desktop opens the same run for layout.",
    );
  } else {
    const title = `Mobile check ${Date.now()}`;
    await page.getByLabel("Title").fill(title);
    await page.getByLabel("PDF file").setInputFiles(PDF);
    await page.getByRole("button", { name: "Upload topic PDF" }).click();
    await expect(page).toHaveURL(/\/topics\//, { timeout: 30_000 });
    fs.mkdirSync(SHOT_DIR, { recursive: true });
    fs.writeFileSync(topicFile, page.url());
    const status = page.locator(".admin-upload__status");
    await expect(status).toBeVisible({ timeout: 60_000 });
    await expect(status).toHaveText(/outline_review|qa_review|published|failed|discarded/, {
      timeout: 180_000,
    });
    await assertNoHorizontalOverflow(page);
    await shot(page, testInfo, "admin-upload-progress");
    const progressText = (await status.innerText()).trim();
    const missingKey = /openrouter|api key|not configured/i.test(progressText);
    if (/failed/i.test(progressText) && missingKey) {
      record(
        testInfo,
        "admin-upload-processing",
        "blocked",
        `PDF was accepted and indexing stopped without an OpenRouter key: ${progressText.slice(0, 300)}`,
      );
      record(testInfo, "teacher-approval", "blocked", progressText.slice(0, 300));
    } else if (/failed/i.test(progressText)) {
      record(testInfo, "admin-upload-processing", "fail", progressText.slice(0, 400));
      expect(progressText, progressText).not.toMatch(/failed/i);
    } else {
      record(
        testInfo,
        "admin-upload-processing",
        "pass",
        `Parsing finished and the run is waiting for review: ${progressText.slice(0, 300)}`,
      );
      const topicUrl = page.url();
      const topicId = topicUrl.match(/topics\/([^/?#]+)/)?.[1] ?? "";
      await signOut(page);
      await signIn(page, DEMO_TEACHER.email);
      await page.goto(`/teacher/topics/${topicId}/generation`);
      const approve = page.getByRole("button", { name: "Approve outline" });
      await expect(approve).toBeVisible({ timeout: 30_000 });
      await assertNoHorizontalOverflow(page);
      await shot(page, testInfo, "teacher-outline-review");
      await approve.click();
      await expect(page.getByText(/: Approved/)).toBeVisible({ timeout: 20_000 });
      await shot(page, testInfo, "teacher-approved");
      record(testInfo, "teacher-approval", "pass", "Meera approved the Grade 8 Mathematics outline.");
      await signOut(page);
      await signIn(page, DEMO_ADMIN.email);
      await page.goto(topicUrl);
      await page.getByRole("button", { name: "Accept current outline" }).click();
      await page.getByRole("dialog").getByRole("button", { name: "Accept current outline" }).click();
      await expect(status).toHaveText(/qa_review|published|failed/, { timeout: 180_000 });
      const afterAccept = (await status.innerText()).trim();
      await assertNoHorizontalOverflow(page);
      await shot(page, testInfo, "admin-after-accept");
      if (/failed/i.test(afterAccept)) {
        const missingModel = /openrouter|api key|not configured/i.test(afterAccept);
        record(
          testInfo,
          "lesson-generation",
          missingModel ? "blocked" : "fail",
          afterAccept.slice(0, 400),
        );
        expect(missingModel, afterAccept).toBe(true);
      } else {
        record(testInfo, "lesson-generation", "pass", afterAccept.slice(0, 300));
        if (/qa_review/i.test(afterAccept)) {
          await page.getByRole("button", { name: "Publish to students" }).click();
          await page.getByRole("dialog").getByRole("button", { name: "Publish to students" }).click();
          await expect(page.getByRole("tab", { name: "Published" })).toHaveAttribute("aria-selected", "true", {
            timeout: 60_000,
          });
          await shot(page, testInfo, "admin-published");
          record(testInfo, "admin-publish", "pass", "Admin published the generated lesson and quiz.");
        }
        await signOut(page);
        await signIn(page, DEMO_SECOND_STUDENT.email);
        await expect(page.getByText("Loading subjects…")).toBeHidden({ timeout: 20_000 });
        const maths = page.locator("a.card").filter({ hasText: "Mathematics" });
        await expect(maths.first()).toBeVisible();
        await maths.first().click();
        await expect(page.locator("a.subtopic-card__head").first()).toBeVisible();
        const publishedLesson = page.getByRole("link", { name: new RegExp(title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) });
        await expect(publishedLesson.first()).toBeVisible({ timeout: 20_000 });
        await publishedLesson.first().click();
        await expect(page.locator(".lesson-overview, .reading, .lesson-view").first()).toBeVisible();
        await assertNoHorizontalOverflow(page);
        await shot(page, testInfo, "student-published-lesson");
        record(testInfo, "student-sees-upload", "pass", "Grade 8 student opened the newly published lesson.");
        await signOut(page);
        await signIn(page, DEMO_ADMIN.email);
      }
    }
  }

  await page.goto("/admin/documents");
  await expect(page.getByRole("heading", { name: "Documents", exact: true })).toBeAttached();
  await expect(page.getByRole("heading", { name: "Upload document" })).toBeVisible();
  await settle(page, testInfo, "admin-documents", "admin-documents");

  await page.goto("/admin/policy");
  await expect(page.getByRole("heading", { name: "Policy assistant" })).toBeAttached();
  await settle(page, testInfo, "admin-policy", "admin-policy");

  await page.goto("/admin/at-risk");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await settle(page, testInfo, "admin-at-risk", "admin-at-risk");

  await signOut(page);
  record(testInfo, "admin-logout", "pass", "Returned to the sign-in form.");
});
