import type { FeedbackBadge, FeedbackBand, FeedbackTrend } from "../api/types";

export function scoreNumber(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  const number = typeof value === "string" ? Number(value) : value;
  return Number.isFinite(number) ? number : null;
}

export function formatScore(value: string | number | null | undefined): string {
  const number = scoreNumber(value);
  if (number === null) return "Not attempted";
  return `${Math.round(number)}%`;
}

/** One phrase. A missing trend stays blank instead of "declining · -100%". */
export function formatTrend(
  trend: FeedbackTrend,
  delta: string | number | null | undefined,
): string | null {
  if (trend === null || delta === null || delta === undefined) return null;
  const points = Math.abs(Math.round(scoreNumber(delta) ?? 0));
  if (points === 0 || trend === "flat") return "No change since the previous attempt";
  switch (trend) {
    case "improving":
      return `Up ${points} points since the previous attempt`;
    case "declining":
      return `Down ${points} points since the previous attempt`;
    default: {
      const unreachable: never = trend;
      return unreachable;
    }
  }
}

export function badgeLabel(badge: FeedbackBadge): string {
  switch (badge) {
    case "not_attempted":
      return "Not attempted";
    case "passed":
      return "Passed";
    case "keep_going":
      return "Keep going";
    default: {
      const unreachable: never = badge;
      return unreachable;
    }
  }
}

export function bandLabel(band: FeedbackBand): string {
  switch (band) {
    case "strong":
      return "Strong";
    case "okay":
      return "Okay";
    case "needs_work":
      return "Needs work";
    default: {
      const unreachable: never = band;
      return unreachable;
    }
  }
}
