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

type AttemptTitle = { label: string; emoji: string };

/** Same titles the old subtopic cards used, now earned by the topic quiz. */
function attemptTitle(attempts: number, bestScore: number): AttemptTitle {
  const band: "perfect" | "high" | "pass" =
    bestScore === 100 ? "perfect" : bestScore >= 90 ? "high" : "pass";
  const titles: Record<typeof band, AttemptTitle[]> = {
    perfect: [
      { label: "Flawless", emoji: "👑" },
      { label: "Diamond", emoji: "💎" },
      { label: "Tenacious", emoji: "🏅" },
      { label: "Champion", emoji: "🥇" },
      { label: "Legendary", emoji: "🏆" },
    ],
    high: [
      { label: "Star Performer", emoji: "🌟" },
      { label: "Sharp Mind", emoji: "🔥" },
      { label: "Rising Star", emoji: "🚀" },
      { label: "Breakthrough", emoji: "💡" },
      { label: "Hard Earned", emoji: "⭐" },
    ],
    pass: [
      { label: "First Try!", emoji: "🎯" },
      { label: "Quick Learner", emoji: "⚡" },
      { label: "Persistent", emoji: "💪" },
      { label: "Determined", emoji: "🔄" },
      { label: "Never Give Up", emoji: "🌱" },
    ],
  };
  return titles[band][Math.min(Math.max(attempts, 1) - 1, 4)];
}

export function attemptBadge(
  badge: FeedbackBadge,
  attemptCount: number,
  best: string | number | null | undefined,
): string {
  if (badge === "not_attempted") return "Not attempted";
  const score = scoreNumber(best);
  if (score === null || badge === "keep_going" || score < 70) return "Keep going";
  const title = attemptTitle(attemptCount, score);
  return `${title.emoji} ${title.label}`;
}

export function achievementCopy(
  tier: "mastery" | "advanced" | "proficient" | "on_track",
): { emoji: string; label: string; detail: string } {
  switch (tier) {
    case "mastery":
      return {
        emoji: "🏆",
        label: "Mastery!",
        detail: "Perfect score — you've completely nailed this subject!",
      };
    case "advanced":
      return {
        emoji: "🔥",
        label: "Advanced!",
        detail: "Excellent work — you're well above the bar!",
      };
    case "proficient":
      return {
        emoji: "⭐",
        label: "Proficient!",
        detail: "Solid understanding across all concepts!",
      };
    case "on_track":
      return {
        emoji: "🌱",
        label: "On Track!",
        detail: "You're passing — keep pushing to go higher!",
      };
    default: {
      const unreachable: never = tier;
      return unreachable;
    }
  }
}

export function calibrationMessage(
  confidence: "still_learning" | "getting_it" | "confident",
  score: number | null,
): string {
  if (score !== null) {
    if (confidence === "confident" && score < 70) {
      return "You're feeling confident — a quick review will help your scores catch up to that confidence.";
    }
    if (confidence === "still_learning" && score >= 80) {
      return 'You said "still learning," but your scores say you\'ve got this — trust yourself a little more!';
    }
    if (confidence === "still_learning" && score >= 70) {
      return "You're doing better than you feel — keep going, you're closer than you think.";
    }
    if (confidence === "confident" && score >= 80) {
      return "Confident and scoring high — you really do know this. Nice.";
    }
  }
  return "Thanks — we saved how you're feeling. You can change it anytime.";
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
