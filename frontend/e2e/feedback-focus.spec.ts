import { expect, test, type Page } from "@playwright/test";

import { DEMO_STUDENT } from "../src/auth/demoAccounts";
import { signIn } from "./support";

type DueSubtopic = { id: string; name: string; topicName: string };

async function expectFocusedInView(page: Page, selector: string) {
  const target = page.locator(selector);
  await expect(target).toBeFocused();
  await expect(target).toHaveClass(/hash-focus-ring/);
  await expect(target).toBeInViewport();
}

test("login, subject, then weak topics and due reviews move focus", async ({ page }) => {
  let dueSubtopic: DueSubtopic | null = null;

  await page.route("**/me/learning-directory", async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as {
      subjects?: {
        name: string;
        topics: { title: string; subtopics: { id: string; title: string }[] }[];
      }[];
    };
    const subject = body.subjects?.find((item) => item.name === "Mathematics") ?? body.subjects?.[0];
    const topic = subject?.topics.find((item) => item.subtopics.length > 0) ?? subject?.topics[0];
    const subtopic = topic?.subtopics[0];
    if (topic && subtopic) {
      dueSubtopic = { id: subtopic.id, name: subtopic.title, topicName: topic.title };
    }
    await route.fulfill({ response, json: body });
  });

  await page.route("**/me/feedback/subjects/**", async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as {
      topics?: { topic_name: string }[];
      topics_needing_attention?: string[];
    };
    const topicName = body.topics?.[0]?.topic_name ?? dueSubtopic?.topicName;
    if (topicName && (body.topics_needing_attention?.length ?? 0) === 0) {
      body.topics_needing_attention = [topicName];
    }
    await route.fulfill({ response, json: body });
  });

  await page.route("**/me/feedback-highlights", async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as {
      due_reviews?: { subtopic_id: string }[];
      trajectories?: unknown[];
    };
    const review = dueSubtopic;
    if (review && !body.due_reviews?.some((item) => item.subtopic_id === review.id)) {
      body.due_reviews = [
        ...(body.due_reviews ?? []),
        {
          subtopic_id: review.id,
          subtopic_name: review.name,
          topic_name: review.topicName,
          days_since_last_attempt: 14,
          due_interval_days: 7,
          remediation: null,
        },
      ];
    }
    await route.fulfill({ response, json: body });
  });

  await signIn(page, DEMO_STUDENT.email);
  await expect(page.getByText("Loading subjects…")).toBeHidden({ timeout: 20_000 });
  await page.getByRole("link", { name: "Mathematics" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Mathematics" })).toBeVisible();

  const due = page.getByRole("link", { name: "Due reviews" });
  const weak = page.getByRole("link", { name: "Weak topics" });
  await expect(due).toBeVisible();
  await expect(weak).toBeVisible();

  await weak.click();
  await expect(page).toHaveURL(/\/subjects\/.+\/feedback#needs-attention$/);
  await expectFocusedInView(page, "#needs-attention");

  await page.goBack();
  await expect(page.getByRole("heading", { level: 1, name: "Mathematics" })).toBeVisible();
  await due.click();
  await expect(page).toHaveURL(/section=due/);
  await expect(page).toHaveURL(/#feedback-due$/);
  await expectFocusedInView(page, "#feedback-due-heading");
  await page.unrouteAll({ behavior: "ignoreErrors" });
});
