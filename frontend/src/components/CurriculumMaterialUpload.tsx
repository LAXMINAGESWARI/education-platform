import { useEffect, useRef, useState, type FormEvent } from "react";

import {
  getMaterialVersionStatus,
  isTerminalIngestStatus,
  watchMaterialVersionStatus,
  uploadSubtopicMaterial,
} from "../api/adminIngest";
import { ProgressReconnectError } from "../api/progress";
import type { ProgressEnvelope } from "../api/progressEnvelope";
import { ApiError, type MaterialVersionStatus } from "../api/types";
import { IngestStatusBadge } from "./IngestStatusBadge";
import { ProgressReconnectStatus, ProgressStreamAlert } from "./ProgressReconnect";
import { PushButton } from "./PushButton";
import { UploadProgressMeter } from "./UploadProgressMeter";

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
  const [streamError, setStreamError] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [counts, setCounts] = useState<ProgressEnvelope | null>(null);
  const [status, setStatus] = useState<MaterialVersionStatus | null>(null);
  const pollAbortRef = useRef<AbortController | null>(null);
  const watchedVersionId = useRef<string | null>(null);

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
    setStreamError(null);
    setReconnecting(false);
    setCounts(null);
    setStatus(null);

    pollAbortRef.current?.abort();
    const abort = new AbortController();
    pollAbortRef.current = abort;

    try {
      const accepted = await uploadSubtopicMaterial(targetSubtopic, file, resolvedTitle);
      if (abort.signal.aborted) return;
      watchedVersionId.current = accepted.version_id;
      setStatus({
        id: accepted.version_id,
        source_material_id: accepted.source_material_id,
        version_number: accepted.version_number,
        title: accepted.title,
        lifecycle_status: accepted.lifecycle_status,
        failure_reason: null,
        chunk_count: 0,
      });
      await followVersion(accepted.version_id, abort);
      if (abort.signal.aborted) return;
      // Resume uses followVersion directly and must keep the chosen file.
      setFile(null);
    } catch (err) {
      if (abort.signal.aborted || (err instanceof DOMException && err.name === "AbortError")) return;
      noteFailure(err, "Upload failed. Try again.");
    } finally {
      if (!abort.signal.aborted) setBusy(false);
    }
  }

  async function followVersion(versionId: string, abort: AbortController) {
    const settled = await watchMaterialVersionStatus(versionId, {
      signal: abort.signal,
      onReconnecting: () => {
        if (!abort.signal.aborted) setReconnecting(true);
      },
      onReconnected: () => {
        // First frame, not response headers, so the banner does not flicker.
        if (!abort.signal.aborted) setReconnecting(false);
      },
      onProgress: (progress) => {
        if (!abort.signal.aborted) setCounts(progress);
      },
    });
    if (abort.signal.aborted) return;
    setReconnecting(false);
    setStreamError(null);
    setStatus(settled);
    onSettled?.(settled);
    if (settled.failure_reason) {
      setError(settled.failure_reason);
    }
  }

  function noteFailure(err: unknown, fallback: string) {
    setReconnecting(false);
    if (err instanceof ProgressReconnectError) {
      setStreamError(err.message);
      return;
    }
    setStreamError(null);
    setError(err instanceof ApiError ? err.message : fallback);
  }

  async function resumeProgress() {
    const versionId = watchedVersionId.current;
    if (!versionId || busy) return;
    pollAbortRef.current?.abort();
    const abort = new AbortController();
    pollAbortRef.current = abort;
    setBusy(true);
    setError(null);
    setStreamError(null);
    setReconnecting(false);
    setCounts(null);
    try {
      const fresh = await getMaterialVersionStatus(versionId);
      if (abort.signal.aborted) return;
      setStatus(fresh);
      if (isTerminalIngestStatus(fresh.lifecycle_status)) {
        onSettled?.(fresh);
        if (fresh.failure_reason) setError(fresh.failure_reason);
        return;
      }
      await followVersion(versionId, abort);
    } catch (err) {
      if (abort.signal.aborted || (err instanceof DOMException && err.name === "AbortError")) return;
      noteFailure(err, "Could not resume progress updates.");
    } finally {
      if (!abort.signal.aborted) setBusy(false);
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
      {streamError ? (
        <ProgressStreamAlert message={streamError} busy={busy} onRetry={() => void resumeProgress()} />
      ) : null}
      <ProgressReconnectStatus active={reconnecting} />
      <UploadProgressMeter envelope={counts} />
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
