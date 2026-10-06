// api/engagement.ts
// Records how long a student actively spent on each lesson slide, and reads
// back the engagement rate for the feedback page.
// Uses the shared apiRequest client (handles base URL, /api/v1 prefix, auth).

import { apiRequest } from "./client";

export interface SlideEngagementIn {
  slide_index: number;
  seconds_active: number;
}

export interface EngagementRate {
  subtopic_id: string;
  engaged_slides: number;
  total_slides: number;
  engagement_rate: number | null; // 0.0–1.0, or null when the lesson has no slides
}

/**
 * POST /api/v1/me/lessons/{subtopicId}/engagement
 * Records active time spent on a single slide. Non-blocking / best-effort.
 */
export async function recordSlideEngagement(
  subtopicId: string,
  slideIndex: number,
  secondsActive: number,
): Promise<void> {
  if (secondsActive < 2) return; // ignore sub-2s noise
  try {
    await apiRequest<{ recorded: boolean }>(
      `/me/lessons/${encodeURIComponent(subtopicId)}/engagement`,
      {
        method: "POST",
        body: {
          slide_index: slideIndex,
          seconds_active: Math.round(secondsActive),
        },
      },
    );
  } catch {
    /* non-blocking — never interrupt the student's flow */
  }
}

/**
 * GET /api/v1/me/lessons/{subtopicId}/engagement-rate
 * Returns the engagement rate for a subtopic (engaged slides / total slides).
 * Returns null on any error so the feedback page degrades gracefully.
 */
export async function getEngagementRate(
  subtopicId: string,
): Promise<EngagementRate | null> {
  try {
    return await apiRequest<EngagementRate>(
      `/me/lessons/${encodeURIComponent(subtopicId)}/engagement-rate`,
    );
  } catch {
    return null;
  }
}
