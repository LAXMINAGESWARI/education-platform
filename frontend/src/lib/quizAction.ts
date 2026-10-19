import type { AttemptHistoryItem, QuizSummary } from "../api/types";

const UNFINISHED = new Set(["in_progress", "abandoned"]);

export function trackedAttempts(attempts: AttemptHistoryItem[]): AttemptHistoryItem[] {
  return attempts.filter((attempt) => attempt.status !== "abandoned");
}

export function passedBestLabel(quiz: QuizSummary | null | undefined): string | null {
  if (!quiz?.passed) return null;
  if (quiz.best_score_percent == null) return "Passed";
  return `Passed · best ${Math.round(Number(quiz.best_score_percent))}%`;
}

export function latestRetakeLabel(quiz: QuizSummary | null | undefined): string | null {
  if (!quiz?.passed) return null;
  const latest = trackedAttempts(quiz.recent_attempts)[0];
  if (!latest || latest.passed !== false) return null;
  const whenRaw = latest.submitted_at ?? latest.started_at;
  const when = whenRaw
    ? new Date(whenRaw).toLocaleDateString("en-GB", { day: "numeric", month: "short" })
    : null;
  const score =
    latest.score_percent == null ? null : `${Math.round(Number(latest.score_percent))}%`;
  const tail = [when, score].filter((part) => part).join(" · ");
  return tail ? `Latest retake ${tail}` : "Latest retake";
}

export function quizActionLabel(quiz: QuizSummary | null | undefined): string {
  const hasFinished = Boolean(
    trackedAttempts(quiz?.recent_attempts ?? []).some(
      (attempt) => !UNFINISHED.has(attempt.status),
    ),
  );
  return hasFinished ? "Retake quiz" : "Start quiz";
}
