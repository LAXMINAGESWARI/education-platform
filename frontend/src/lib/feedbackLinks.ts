import type { ReviewNudgeOut, TopicNode } from "../api/types";

export function subjectFeedbackPath(subjectId: string): string {
  return `/subjects/${subjectId}/feedback`;
}

export function topicFeedbackPath(subjectId: string, topicId: string): string {
  return `/subjects/${subjectId}/feedback/topics/${topicId}`;
}

export function dueReviewsPath(subjectId: string): string {
  const params = new URLSearchParams({ subject: subjectId, section: "due" });
  return `/feedback?${params.toString()}#feedback-due`;
}

export function weakTopicsPath(subjectId: string): string {
  return `/subjects/${subjectId}/feedback#needs-attention`;
}

/** Topics on this subject that have a subtopic past its review interval. */
export function dueTopicIds(topics: TopicNode[], reviews: ReviewNudgeOut[]): string[] {
  const bySubtopic = new Map<string, string>();
  const byName = new Map<string, string>();
  for (const topic of topics) {
    byName.set(topic.title, topic.id);
    for (const subtopic of topic.subtopics) {
      bySubtopic.set(subtopic.id, topic.id);
    }
  }
  const due = new Set<string>();
  for (const review of reviews) {
    const topicId = bySubtopic.get(review.subtopic_id) ?? byName.get(review.topic_name);
    if (topicId) due.add(topicId);
  }
  return [...due];
}
