import { scoreNumber } from "./feedbackHierarchy";

export type BadgeTone = "ok" | "warn" | "info" | "locked" | "danger" | "neutral";

export type StatusKind =
  | "complete"
  | "in_progress"
  | "not_started"
  | "coming_soon"
  | "not_attempted"
  | "mastery"
  | "advanced"
  | "proficient"
  | "on_track"
  | "needs_work"
  | "improving"
  | "flat"
  | "declining"
  | "due";

export const STATUS_PRESENTATION: Record<StatusKind, { label: string; tone: BadgeTone }> = {
  complete: { label: "Complete", tone: "ok" },
  in_progress: { label: "In progress", tone: "warn" },
  not_started: { label: "Not started", tone: "locked" },
  coming_soon: { label: "Coming soon", tone: "locked" },
  not_attempted: { label: "Not attempted", tone: "info" },
  mastery: { label: "Mastery", tone: "ok" },
  advanced: { label: "Advanced", tone: "ok" },
  proficient: { label: "Proficient", tone: "info" },
  on_track: { label: "On track", tone: "info" },
  needs_work: { label: "Needs work", tone: "warn" },
  improving: { label: "Improving", tone: "ok" },
  flat: { label: "No change", tone: "locked" },
  declining: { label: "Declining", tone: "danger" },
  due: { label: "Due", tone: "warn" },
};

export type ScoreBand = {
  tone: BadgeTone;
  rating: string;
};

/** Pass bar is 70%. 80% and above reads as strong. */
export function scoreBand(value: string | number | null | undefined): ScoreBand | null {
  const score = scoreNumber(value);
  if (score === null) return null;
  if (Math.round(score) >= 80) return { tone: "ok", rating: "Strong" };
  if (Math.round(score) >= 70) return { tone: "info", rating: "On track" };
  return { tone: "warn", rating: "Needs work" };
}
