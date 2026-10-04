// api/engagement.ts
// Records how long a student actively spent on each lesson slide.
// Used by the Struggle Flag system as a pre-quiz engagement signal.

const API_BASE = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

function getToken(): string | null {
  return localStorage.getItem("access_token");
}

export interface SlideEngagementIn {
  slide_index: number;
  seconds_active: number;
}

/**
 * POST /api/v1/me/lessons/{subtopicId}/engagement
 * Records active time spent on a single slide.
 * Fire-and-forget — caller should not await in UI-critical paths.
 */
export async function recordSlideEngagement(
  subtopicId: string,
  slideIndex: number,
  secondsActive: number,
): Promise<void> {
  const token = getToken();
  if (!token || secondsActive < 2) return; // ignore sub-2s noise

  await fetch(`${API_BASE}/api/v1/me/lessons/${subtopicId}/engagement`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      slide_index: slideIndex,
      seconds_active: Math.round(secondsActive),
    }),
    // keepalive so the request survives page unload
    keepalive: true,
  });
}
