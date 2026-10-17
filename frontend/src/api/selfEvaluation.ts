// api/selfEvaluation.ts
// Student confidence self-check per subject (self-evaluation).
// Uses the shared apiRequest client (base URL, /api/v1 prefix, auth).

import { apiRequest } from "./client";

export type ConfidenceLevel = "still_learning" | "getting_it" | "confident";

export interface SelfEvaluation {
  subject_id: string;
  confidence: ConfidenceLevel | null;
}

/** GET the student's latest confidence rating for a subject (null if never set). */
export async function getSelfEvaluation(
  subjectId: string,
): Promise<SelfEvaluation | null> {
  try {
    return await apiRequest<SelfEvaluation>(
      `/me/subjects/${encodeURIComponent(subjectId)}/self-evaluation`,
    );
  } catch {
    return null;
  }
}

/** POST a new confidence rating for a subject. */
export async function recordSelfEvaluation(
  subjectId: string,
  confidence: ConfidenceLevel,
): Promise<SelfEvaluation | null> {
  try {
    return await apiRequest<SelfEvaluation>(
      `/me/subjects/${encodeURIComponent(subjectId)}/self-evaluation`,
      { method: "POST", body: { confidence } },
    );
  } catch {
    return null;
  }
}
