import type { ProgressEnvelope } from "../api/progressEnvelope";

const UNIT_LABEL: Record<string, string> = {
  chunk: "chunks",
  page: "pages",
};

/** "16 of 64 chunks" when the envelope has a count. Null counts stay hidden. */
export function uploadProgressLabel(envelope: ProgressEnvelope): string | null {
  if (envelope.current === null || envelope.total === null || envelope.total <= 0) return null;
  const unit = envelope.unit === null ? null : (UNIT_LABEL[envelope.unit] ?? envelope.unit);
  if (unit === null) return `${envelope.current} of ${envelope.total}`;
  return `${envelope.current} of ${envelope.total} ${unit}`;
}

export function UploadProgressMeter({ envelope }: { envelope: ProgressEnvelope | null }) {
  if (envelope === null) return null;
  const label = uploadProgressLabel(envelope);
  if (label === null) return null;
  const current = envelope.current ?? 0;
  const total = envelope.total ?? 0;
  return (
    <div className="upload-progress">
      <p className="upload-progress__count">{label}</p>
      <progress className="upload-progress__meter" max={total} value={current} aria-label={label} />
    </div>
  );
}
