import { useEffect, useRef, useState, type FormEvent } from "react";

import {
  watchMaterialVersionStatus,
  uploadSubtopicMaterial,
} from "../api/adminIngest";
import { ApiError, type MaterialVersionStatus } from "../api/types";
import { IngestStatusBadge } from "./IngestStatusBadge";
import { PushButton } from "./PushButton";

export type SubtopicOption = {
  id: string;
  label: string;
};

type CurriculumMaterialUploadProps = {
  /** When set, skip the subtopic selector. */
  subtopicId?: string;
  subtopicOptions?: SubtopicOption[];
  defaultTitle?: string;
  compact?: boolean;
  onSettled?: (status: MaterialVersionStatus) => void;
};

export function CurriculumMaterialUpload({
  subtopicId: fixedSubtopicId,
  subtopicOptions,
  defaultTitle = "",
  compact = false,
  onSettled,
}: CurriculumMaterialUploadProps) {
  const [subtopicId, setSubtopicId] = useState(fixedSubtopicId ?? "");
  const [title, setTitle] = useState(defaultTitle);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<MaterialVersionStatus | null>(null);
  const pollAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (fixedSubtopicId) setSubtopicId(fixedSubtopicId);
  }, [fixedSubtopicId]);

  useEffect(() => {
    setTitle(defaultTitle);
  }, [defaultTitle]);

  useEffect(() => {
    return () => {
      pollAbortRef.current?.abort();
    };
  }, []);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const targetSubtopic = fixedSubtopicId ?? subtopicId;
    if (!targetSubtopic || !file || busy) return;

    const resolvedTitle = title.trim() || file.name.replace(/\.pdf$/i, "");
    setBusy(true);
    setError(null);
    setStatus(null);

    pollAbortRef.current?.abort();
    const abort = new AbortController();
    pollAbortRef.current = abort;

    try {
      const accepted = await uploadSubtopicMaterial(targetSubtopic, file, resolvedTitle);
      setStatus({
        id: accepted.version_id,
        source_material_id: accepted.source_material_id,
        version_number: accepted.version_number,
        title: accepted.title,
        lifecycle_status: accepted.lifecycle_status,
        failure_reason: null,
        chunk_count: 0,
      });
      const settled = await watchMaterialVersionStatus(accepted.version_id, {
        signal: abort.signal,
      });
      setStatus(settled);
      onSettled?.(settled);
      if (settled.failure_reason) {
        setError(settled.failure_reason);
      }
      setFile(null);
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setError(err instanceof ApiError ? err.message : "Upload failed. Try again.");
    } finally {
      setBusy(false);
    }
  }

  const canSubmit = Boolean((fixedSubtopicId ?? subtopicId) && file && !busy);

  return (
    <form
      className={`admin-upload ${compact ? "admin-upload--compact" : ""}`}
      onSubmit={(e) => void onSubmit(e)}
    >
      {!fixedSubtopicId && (
        <div className="form__field">
          <label className="form__label" htmlFor="curriculum-upload-subtopic">
            Subtopic
          </label>
          <select
            id="curriculum-upload-subtopic"
            className="form__input"
            value={subtopicId}
            onChange={(e) => setSubtopicId(e.target.value)}
            disabled={busy}
            required
          >
            <option value="">Select a subtopic…</option>
            {(subtopicOptions ?? []).map((opt) => (
              <option key={opt.id} value={opt.id}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className="form__field">
        <label className="form__label" htmlFor="curriculum-upload-title">
          Title
        </label>
        <input
          id="curriculum-upload-title"
          className="form__input"
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Lesson title"
          disabled={busy}
        />
      </div>
      <div className="form__field">
        <label className="form__label" htmlFor="curriculum-upload-file">
          PDF file
        </label>
        <input
          id="curriculum-upload-file"
          className="form__input"
          type="file"
          accept="application/pdf,.pdf"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          disabled={busy}
        />
      </div>
      {error && (
        <p className="form__error" role="alert">
          {error}
        </p>
      )}
      {status && (
        <p className="admin-upload__status" role="status">
          Ingest: <IngestStatusBadge status={status.lifecycle_status} />
          {status.chunk_count > 0 ? ` · ${status.chunk_count} chunks` : null}
          {status.failure_reason ? ` · ${status.failure_reason}` : null}
        </p>
      )}
      <div className="admin-upload__actions">
        <PushButton type="submit" disabled={!canSubmit} loading={busy}>
          {busy ? "Processing…" : "Upload PDF"}
        </PushButton>
      </div>
    </form>
  );
}
