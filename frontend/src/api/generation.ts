import { apiRequest } from "./client";
import { runSubject, watchProgress, type ProgressReconnectNotice } from "./progress";
import type { ProgressEnvelope } from "./progressEnvelope";
import type {
  AcceptedGenerationRun,
  CloseRoundCommand,
  CloseRoundResult,
  GenerationAssistantReply,
  GenerationAssistantTurn,
  GenerationRun,
  GenerationStepDetail,
  GenerationStepTiming,
  PublishedTopic,
  ReviewWorkspace,
  RunPhase,
  TeacherDecision,
  TeacherDecisionCommand,
  TopicItemStats,
} from "./types";

export function isInFlightGenerationPhase(phase: RunPhase): boolean {
  switch (phase) {
    case "indexing":
    case "outlining":
    case "outline_review":
    case "generating":
    case "qa_review":
      return true;
    case "failed":
    case "discarded":
    case "published":
      return false;
    default: {
      const _never: never = phase;
      void _never;
      return false;
    }
  }
}

export function isTerminalGenerationPhase(phase: RunPhase): boolean {
  switch (phase) {
    case "indexing":
    case "outlining":
      return false;
    case "outline_review":
    case "failed":
    case "discarded":
    case "generating":
    case "qa_review":
    case "published":
      return true;
    default: {
      const _never: never = phase;
      void _never;
      return false;
    }
  }
}

export async function submitTopicGenerationRun(
  topicId: string,
  file: File,
  title: string,
  targetItemCount?: number,
): Promise<AcceptedGenerationRun> {
  const body = new FormData();
  body.append("file", file);
  body.append("title", title);
  if (targetItemCount != null) {
    body.append("target_item_count", String(targetItemCount));
  }
  return apiRequest<AcceptedGenerationRun>(
    `/admin/topics/${encodeURIComponent(topicId)}/generation-runs`,
    { method: "POST", body },
  );
}

export async function submitSubjectGenerationRun(
  subjectId: string,
  file: File,
  title: string,
  targetItemCount?: number,
  gradeName?: string,
): Promise<AcceptedGenerationRun> {
  const body = new FormData();
  body.append("file", file);
  body.append("title", title);
  if (targetItemCount != null) {
    body.append("target_item_count", String(targetItemCount));
  }
  if (gradeName) {
    body.append("grade_name", gradeName);
  }
  return apiRequest<AcceptedGenerationRun>(
    `/admin/subjects/${encodeURIComponent(subjectId)}/generation-runs`,
    { method: "POST", body },
  );
}

export async function listGenerationSteps(runId: string): Promise<GenerationStepTiming[]> {
  return apiRequest<GenerationStepTiming[]>(
    `/admin/generation-runs/${encodeURIComponent(runId)}/steps`,
  );
}

export async function getGenerationStepDetail(
  runId: string,
  step: string,
): Promise<GenerationStepDetail> {
  return apiRequest<GenerationStepDetail>(
    `/admin/generation-runs/${encodeURIComponent(runId)}/steps/${encodeURIComponent(step)}`,
  );
}

export async function getGenerationRun(runId: string): Promise<GenerationRun> {
  return apiRequest<GenerationRun>(`/teaching/generation-runs/${encodeURIComponent(runId)}`);
}

export async function listTopicGenerationRuns(topicId: string): Promise<GenerationRun[]> {
  return apiRequest<GenerationRun[]>(
    `/teaching/topics/${encodeURIComponent(topicId)}/generation-runs`,
  );
}

export async function getGenerationReview(runId: string): Promise<ReviewWorkspace> {
  return apiRequest<ReviewWorkspace>(
    `/teaching/generation-runs/${encodeURIComponent(runId)}/review`,
  );
}

export async function submitReviewDecision(
  runId: string,
  roundId: string,
  command: TeacherDecisionCommand,
): Promise<TeacherDecision> {
  return apiRequest<TeacherDecision>(
    `/teaching/generation-runs/${encodeURIComponent(runId)}/review-rounds/${encodeURIComponent(roundId)}/decisions`,
    { method: "POST", body: command },
  );
}

export async function closeReviewRound(
  runId: string,
  roundId: string,
  command: CloseRoundCommand,
): Promise<CloseRoundResult> {
  return apiRequest<CloseRoundResult>(
    `/teaching/generation-runs/${encodeURIComponent(runId)}/review-rounds/${encodeURIComponent(roundId)}/close`,
    { method: "POST", body: command },
  );
}

export async function askGenerationAssistant(
  runId: string,
  command: GenerationAssistantTurn,
): Promise<GenerationAssistantReply> {
  return apiRequest<GenerationAssistantReply>(
    `/teaching/generation-runs/${encodeURIComponent(runId)}/assistant/turns`,
    { method: "POST", body: command },
  );
}

export async function acceptGenerationOutline(runId: string): Promise<GenerationRun> {
  return apiRequest<GenerationRun>(
    `/teaching/generation-runs/${encodeURIComponent(runId)}/accept-outline`,
    { method: "POST" },
  );
}

export async function discardGenerationRun(runId: string): Promise<GenerationRun> {
  return apiRequest<GenerationRun>(
    `/teaching/generation-runs/${encodeURIComponent(runId)}/discard`,
    { method: "POST" },
  );
}

export async function deleteUnpublishedTopic(topicId: string): Promise<void> {
  await apiRequest<void>(`/admin/topics/${encodeURIComponent(topicId)}`, {
    method: "DELETE",
  });
}

export async function retryGenerationRun(runId: string): Promise<GenerationRun> {
  return apiRequest<GenerationRun>(
    `/teaching/generation-runs/${encodeURIComponent(runId)}/retry`,
    { method: "POST" },
  );
}

export async function rejectGenerationItems(
  runId: string,
  questionIds: string[],
): Promise<GenerationRun> {
  return apiRequest<GenerationRun>(
    `/teaching/generation-runs/${encodeURIComponent(runId)}/reject-items`,
    { method: "POST", body: { question_ids: questionIds } },
  );
}

export async function fetchTopicItemStats(topicId: string): Promise<TopicItemStats> {
  return apiRequest<TopicItemStats>(
    `/admin/topics/${encodeURIComponent(topicId)}/item-stats`,
  );
}

export async function publishGenerationRun(runId: string): Promise<PublishedTopic> {
  return apiRequest<PublishedTopic>(
    `/teaching/generation-runs/${encodeURIComponent(runId)}/publish`,
    { method: "POST" },
  );
}

export async function subscribeGenerationRun(
  runId: string,
  options: {
    signal?: AbortSignal;
    onSnapshot?: (run: GenerationRun) => void;
    onProgress?: (progress: ProgressEnvelope) => void;
    onReconnecting?: (notice: ProgressReconnectNotice) => void;
    /** Fires on the first frame of a connection, including the first connect. */
    onReconnected?: () => void;
  } = {},
): Promise<GenerationRun> {
  const snapshot = await watchProgress(runSubject(runId), {
    signal: options.signal,
    onReconnecting: options.onReconnecting,
    onReconnected: options.onReconnected,
    onProgress: options.onProgress,
    onSnapshot: (next) => {
      options.onSnapshot?.(next as GenerationRun);
    },
  });
  return snapshot as GenerationRun;
}
