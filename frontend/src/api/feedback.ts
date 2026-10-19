import { apiRequest } from "./client";
import type {
  FeedbackHighlights,
  FeedbackOverview,
  FeedbackSubjectDetail,
  FeedbackTopicDetail,
  GoalOut,
  SubjectFeedbackDashboard,
} from "./types";

export async function getFeedbackOverview(): Promise<FeedbackOverview> {
  return apiRequest<FeedbackOverview>("/me/feedback");
}

export async function getFeedbackSubject(subjectId: string): Promise<FeedbackSubjectDetail> {
  return apiRequest<FeedbackSubjectDetail>(
    `/me/feedback/subjects/${encodeURIComponent(subjectId)}`,
  );
}

export async function getFeedbackTopic(topicId: string): Promise<FeedbackTopicDetail> {
  return apiRequest<FeedbackTopicDetail>(`/me/feedback/topics/${encodeURIComponent(topicId)}`);
}

export async function getSubjectFeedback(subjectId: string): Promise<SubjectFeedbackDashboard> {
  return apiRequest<SubjectFeedbackDashboard>(`/subjects/${encodeURIComponent(subjectId)}/feedback`);
}

export async function getFeedbackHighlights(): Promise<FeedbackHighlights> {
  return apiRequest<FeedbackHighlights>("/me/feedback-highlights");
}

export async function setSubtopicGoal(
  subtopicId: string,
  payload: { target_percent: number; due_at: string },
): Promise<GoalOut> {
  return apiRequest<GoalOut>(`/subtopics/${encodeURIComponent(subtopicId)}/goal`, {
    method: "PUT",
    body: payload,
  });
}

export async function deleteSubtopicGoal(subtopicId: string): Promise<void> {
  await apiRequest<void>(`/subtopics/${encodeURIComponent(subtopicId)}/goal`, {
    method: "DELETE",
  });
}
