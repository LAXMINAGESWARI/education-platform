import { apiRequest } from "./client";
import type { FeedbackHighlights, GoalOut, SubjectFeedbackDashboard } from "./types";

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
