import { describe, expect, it } from "vitest";

import type { TopicNode } from "../api/types";
import { dueReviewsPath, dueTopicIds, subjectFeedbackPath, topicFeedbackPath, weakTopicsPath } from "./feedbackLinks";

const topic: TopicNode = {
  id: "topic-1",
  title: "Fractions",
  slug: "fractions",
  sequence: 1,
  progress_percent: 40,
  complete: false,
  objectives: [],
  subtopics: [
    {
      id: "st-1",
      title: "Practice",
      slug: "practice",
      sequence: 1,
      has_lesson: false,
      lesson_completed: false,
      progress_percent: 0,
      quiz: null,
    },
  ],
  overall_quiz: null,
};

describe("feedbackLinks", () => {
  it("builds subject, topic, due, and weak-topic links", () => {
    expect(subjectFeedbackPath("math-1")).toBe("/subjects/math-1/feedback");
    expect(topicFeedbackPath("math-1", "topic-1")).toBe("/subjects/math-1/feedback/topics/topic-1");
    expect(dueReviewsPath("math-1")).toBe("/feedback?subject=math-1&section=due#feedback-due");
    expect(weakTopicsPath("math-1")).toBe("/subjects/math-1/feedback#needs-attention");
  });

  it("marks a topic due from a subtopic review nudge", () => {
    expect(
      dueTopicIds(
        [topic, { ...topic, id: "topic-2", title: "Decimals", subtopics: [] }],
        [
          {
            subtopic_id: "st-1",
            subtopic_name: "Practice",
            topic_name: "Fractions",
            days_since_last_attempt: 9,
            due_interval_days: 7,
            remediation: null,
          },
        ],
      ),
    ).toEqual(["topic-1"]);
  });
});
