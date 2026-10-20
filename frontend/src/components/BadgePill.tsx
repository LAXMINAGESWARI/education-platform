import type { ReactNode } from "react";

import { scoreBand, STATUS_PRESENTATION, type BadgeTone, type StatusKind } from "../lib/badges";
import { scoreNumber } from "../lib/feedbackHierarchy";

const TONE_CLASS: Record<BadgeTone, string> = {
  ok: "badge--ok",
  warn: "badge--warn",
  info: "badge--info",
  locked: "badge--locked",
  danger: "badge--danger",
  neutral: "",
};

export function BadgePill({ children, tone = "neutral" }: { children: ReactNode; tone?: BadgeTone }) {
  const toneClass = TONE_CLASS[tone];
  return <span className={toneClass ? `badge ${toneClass}` : "badge"}>{children}</span>;
}

export function SubjectBadge({ name, tone = "info" }: { name: string; tone?: BadgeTone }) {
  return <BadgePill tone={tone}>{name}</BadgePill>;
}

export function StatusBadge({ status }: { status: StatusKind }) {
  const item = STATUS_PRESENTATION[status];
  return <BadgePill tone={item.tone}>{item.label}</BadgePill>;
}

/** Percent pill. The rating word is available to assistive tech, not only by color. */
export function ScoreBadge({ value }: { value: string | number | null | undefined }) {
  const score = scoreNumber(value);
  if (score === null) {
    return <BadgePill tone="info">Not attempted</BadgePill>;
  }
  const rounded = Math.round(score);
  const band = scoreBand(value);
  return (
    <BadgePill tone={band?.tone ?? "info"}>
      {rounded}%<span className="sr-only">, {band?.rating}</span>
    </BadgePill>
  );
}

/** Rating word only, for summary tiles that already show the percent as a figure. */
export function RatingBadge({ value }: { value: string | number | null | undefined }) {
  const band = scoreBand(value);
  if (!band) return <BadgePill tone="info">Not attempted</BadgePill>;
  return <BadgePill tone={band.tone}>{band.rating}</BadgePill>;
}

export function EmptyValue() {
  return (
    <span className="muted">
      <span aria-hidden="true">—</span>
      <span className="sr-only">None</span>
    </span>
  );
}
