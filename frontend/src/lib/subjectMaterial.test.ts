import { describe, expect, it } from "vitest";

import type { TopicNode } from "../api/types";
import {
  hasPublishedTopicLesson,
  overallQuizCopy,
  overallQuizGate,
  subjectTopicLessonTopics,
  subjectUnitEntries,
  topicLessonPath,
} from "./subjectMaterial";

const baseTopic: TopicNode = {
  id: "topic-1",
  title: "Approved Materials",
  slug: "approved_materials",
  sequence: 1,
  progress_percent: 25,
  complete: false,
  objectives: [],
  subtopics: [],
  overall_quiz: {
    id: "overall-1",
    title: "Overall",
    scope: "topic_mastery",
    available: true,
    unlocked: false,
    locked_reason: "Pass all subtopic quizzes first",
    pass_threshold_percent: 70,
    attempt_count: 0,
    best_score_percent: null,
    passed: false,
    in_progress_attempt_id: null,
    recent_attempts: [],
  },
};

describe("subjectMaterial topic lesson helpers", () => {
  it("lists published subtopic lessons as units and keeps a parent topic lesson separate", () => {
    const parent: TopicNode = {
      ...baseTopic,
      has_topic_lesson: true,
      subtopics: [
        {
          id: "st-1",
          title: "Rectangles",
          slug: "rectangles",
          sequence: 1,
          has_lesson: true,
          lesson_completed: false,
          progress_percent: 0,
          quiz: null,
        },
        {
          id: "st-2",
          title: "Square Numbers",
          slug: "square-numbers",
          sequence: 2,
          has_lesson: true,
          lesson_completed: false,
          progress_percent: 0,
          quiz: null,
        },
      ],
    };
    const leaf: TopicNode = {
      ...baseTopic,
      id: "topic-2",
      title: "Fractions",
      slug: "fractions",
      sequence: 2,
      has_topic_lesson: false,
      subtopics: [],
      overall_quiz: null,
    };
    expect(subjectUnitEntries([parent, leaf]).map((entry) => entry.topic.title)).toEqual([
      "Approved Materials",
      "Fractions",
    ]);
    expect(subjectTopicLessonTopics([parent, leaf]).map((topic) => topic.title)).toEqual([
      "Approved Materials",
    ]);
  });

  it("omits outline-only analysis nodes and lists one chapter named from the topic", () => {
    const generated: TopicNode = {
      ...baseTopic,
      id: "topic-gen",
      title: "Fractions chapter",
      slug: "fractions-chapter",
      has_topic_lesson: true,
      subtopics: [
        {
          id: "st-outline",
          title: "Like fractions",
          slug: "like-fractions",
          sequence: 1,
          has_lesson: false,
          lesson_completed: false,
          progress_percent: 0,
          quiz: null,
        },
        {
          id: "st-outline-2",
          title: "Unlike fractions",
          slug: "unlike-fractions",
          sequence: 2,
          has_lesson: false,
          lesson_completed: false,
          progress_percent: 0,
          quiz: null,
        },
      ],
    };
    expect(
      subjectUnitEntries([generated]).map((entry) => ({
        title: entry.subtopic?.title ?? entry.topic.title,
        subtopic: entry.subtopic,
      })),
    ).toEqual([{ title: "Fractions chapter", subtopic: null }]);
    expect(subjectTopicLessonTopics([generated])).toEqual([]);
  });

  it("keeps seeded has_lesson units and skips sibling outline-only nodes", () => {
    const mixed: TopicNode = {
      ...baseTopic,
      has_topic_lesson: true,
      subtopics: [
        {
          id: "st-1",
          title: "Rectangles",
          slug: "rectangles",
          sequence: 1,
          has_lesson: true,
          lesson_completed: false,
          progress_percent: 0,
          quiz: null,
        },
        {
          id: "st-outline",
          title: "Like fractions",
          slug: "like-fractions",
          sequence: 2,
          has_lesson: false,
          lesson_completed: false,
          progress_percent: 0,
          quiz: null,
        },
      ],
    };
    expect(subjectUnitEntries([mixed]).map((entry) => entry.topic.title)).toEqual([
      "Approved Materials",
    ]);
    expect(subjectTopicLessonTopics([mixed]).map((topic) => topic.title)).toEqual(["Approved Materials"]);
  });

  it("builds the student topic lesson route", () => {
    expect(topicLessonPath("subj-1", "topic-1")).toBe(
      "/subjects/subj-1/topics/topic-1/lesson",
    );
  });

  it("unlocks copy from a published topic lesson, not complete-the-lesson-first", () => {
    const topic: TopicNode = {
      ...baseTopic,
      has_topic_lesson: true,
      topic_source_material_version_id: "ver-1",
      overall_quiz: { ...baseTopic.overall_quiz!, unlocked: true, locked_reason: null },
    };
    expect(hasPublishedTopicLesson(topic)).toBe(true);
    expect(overallQuizGate(topic)).toBe("topic_lesson");
    const copy = overallQuizCopy(topic);
    expect(copy.eyebrow).toBe("From the topic lesson");
    expect(copy.body).toMatch(/topic lesson is complete/i);
    expect(copy.body).not.toMatch(/every unit quiz/i);
  });

  it("keeps the seeded pass-all-subtopic-quizzes gate without a topic lesson", () => {
    expect(overallQuizGate(baseTopic)).toBe("subtopic_quizzes");
    expect(overallQuizCopy(baseTopic).eyebrow).toBe("After subtopic quizzes");
    expect(overallQuizCopy(baseTopic).body).toMatch(/every subtopic quiz/i);
  });
});
