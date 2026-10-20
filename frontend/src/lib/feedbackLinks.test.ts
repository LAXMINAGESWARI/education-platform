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

  it("does not mark the same topic name due in another subject", () => {
    const verbForms = {
      id: "ar-verb",
      title: "Verb forms",
      slug: "verb-forms",
      sequence: 1,
      has_lesson: false,
      lesson_completed: false,
      progress_percent: 0,
      quiz: null,
    };
    const english = {
      ...topic,
      id: "en-grammar",
      title: "Grammar",
      subtopics: [{ ...verbForms, id: "en-verb", title: "Verb forms" }],
    };
    const arabic = {
      ...topic,
      id: "ar-grammar",
      title: "Grammar",
      subtopics: [verbForms],
    };
    const arabicDue = {
      subtopic_id: "ar-verb",
      subtopic_name: "Verb forms",
      topic_name: "Grammar",
      days_since_last_attempt: 12,
      due_interval_days: 7,
      remediation: null,
    };

    expect(dueTopicIds([english, arabic], [arabicDue])).toEqual(["ar-grammar"]);
    expect(dueTopicIds([english], [arabicDue])).toEqual([]);
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
