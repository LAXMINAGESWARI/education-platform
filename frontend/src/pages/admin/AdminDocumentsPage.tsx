import { useCallback, useEffect, useRef, useState, type DragEvent, type FormEvent } from "react";

import {
  listKnowledgeDocuments,
  watchKnowledgeDocumentVersionStatus,
  uploadKnowledgeDocument,
} from "../../api/adminIngest";
import { ProgressReconnectError } from "../../api/progress";
import { ApiError, type KnowledgeDocumentListItem } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { DEMO_ADMIN } from "../../auth/demoAccounts";
import { ROLE_ADMIN, ROLE_STUDENT, ROLE_TEACHER } from "../../auth/roles";
import { IngestStatusBadge } from "../../components/IngestStatusBadge";
import { PushButton } from "../../components/PushButton";

const DOC_TYPES = [
  { value: "policy", label: "Policy" },
  { value: "handbook", label: "Handbook" },
  { value: "other", label: "Other" },
];

const ROLE_OPTIONS = [
  { value: ROLE_ADMIN, label: "Administrator" },
  { value: ROLE_TEACHER, label: "Teacher" },
  { value: ROLE_STUDENT, label: "Student" },
] as const;

const DEFAULT_ROLES = [ROLE_ADMIN, ROLE_TEACHER];

const MOCK_SESSION_DOCS_ERROR =
  `Documents need a real administrator JWT. Sign in as ${DEMO_ADMIN.email} (not a fixture session).`;

function isPdfFile(file: File): boolean {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

function titleFromFile(file: File): string {
  return file.name.replace(/\.pdf$/i, "").replaceAll("_", " ").trim();
}

export function AdminDocumentsPage() {
  const { isDevMockSession } = useAuth();
  const [docs, setDocs] = useState<KnowledgeDocumentListItem[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  const [title, setTitle] = useState("");
  const [docType, setDocType] = useState("policy");
  const [requiredRoles, setRequiredRoles] = useState<string[]>(DEFAULT_ROLES);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const dragDepth = useRef(0);
  const watchAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      watchAbortRef.current?.abort();
    };
  }, []);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    if (signal?.aborted) return;
    if (isDevMockSession) {
      setDocs(null);
      setListError(MOCK_SESSION_DOCS_ERROR);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const items = await listKnowledgeDocuments(signal);
      if (signal?.aborted) return;
      setDocs(items);
      setListError(null);
    } catch (err) {
      if (signal?.aborted) return;
      setDocs(null);
      setListError(
        err instanceof ApiError ? err.message : "Could not load knowledge documents.",
      );
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [isDevMockSession]);

  useEffect(() => {
    const abort = new AbortController();
    void refresh(abort.signal);
    return () => {
      abort.abort();
    };
  }, [refresh]);

  function chooseFile(next: File | null, fromDrop = false) {
    if (!next) {
      setFile(null);
      return;
    }
    if (!isPdfFile(next)) {
      setUploadError("Only PDF files can be uploaded.");
      return;
    }
    setUploadError(null);
    setFile(next);
    if (fromDrop || !title.trim()) {
      setTitle((current) => current.trim() || titleFromFile(next));
    }
  }

  function onDragEnter(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    dragDepth.current += 1;
    setDragging(true);
  }

  function onDragOver(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }

  function onDragLeave(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragging(false);
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    chooseFile(event.dataTransfer.files[0] ?? null, true);
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!file || busy || isDevMockSession) return;

    const resolvedTitle = title.trim() || titleFromFile(file);
    if (requiredRoles.length === 0) {
      setUploadError("Select at least one role.");
      return;
    }

    setBusy(true);
    setUploadError(null);
    setReconnecting(false);
    setUploadStatus("processing");

    watchAbortRef.current?.abort();
    const abort = new AbortController();
    watchAbortRef.current = abort;

    try {
      const accepted = await uploadKnowledgeDocument({
        file,
        title: resolvedTitle,
        docType,
        requiredRoles,
      });
      if (abort.signal.aborted) return;
      setUploadStatus(accepted.lifecycle_status);
      const settled = await watchKnowledgeDocumentVersionStatus(accepted.version_id, {
        signal: abort.signal,
        onReconnecting: () => {
          if (!abort.signal.aborted) setReconnecting(true);
        },
        onReconnected: () => {
          if (!abort.signal.aborted) setReconnecting(false);
        },
      });
      if (abort.signal.aborted) return;
      setReconnecting(false);
      setUploadStatus(settled.lifecycle_status);
      if (settled.failure_reason) {
        setUploadError(settled.failure_reason);
      }
      setFile(null);
      setTitle("");
      await refresh(abort.signal);
    } catch (err) {
      if (abort.signal.aborted || (err instanceof DOMException && err.name === "AbortError")) return;
      setUploadStatus(null);
      setReconnecting(false);
      const message =
        err instanceof ProgressReconnectError || err instanceof ApiError
          ? err.message
          : "Upload failed. Try again.";
      setUploadError(message);
    } finally {
      if (!abort.signal.aborted) setBusy(false);
    }
  }

  return (
    <div className="admin-documents">
      <h1 className="sr-only">Documents</h1>
      <p className="admin-documents__lede">Upload PDFs to index for Policy chat.</p>

      <div className="admin-documents__layout">
        <section className="panel admin-documents__upload" aria-labelledby="docs-upload-heading">
          <h2 id="docs-upload-heading" className="admin-documents__section-title">
            Upload document
          </h2>
          {isDevMockSession ? (
            <p className="form__error" role="alert">
              {MOCK_SESSION_DOCS_ERROR}
            </p>
          ) : (
            <form className="admin-upload" onSubmit={(e) => void onSubmit(e)}>
              <div
                className={`admin-dropzone ${dragging ? "is-dragging" : ""} ${file ? "has-file" : ""}`}
                data-testid="pdf-dropzone"
                onDragEnter={onDragEnter}
                onDragOver={onDragOver}
                onDragLeave={onDragLeave}
                onDrop={onDrop}
              >
                <input
                  id="knowledge-doc-file"
                  className="sr-only"
                  type="file"
                  accept="application/pdf,.pdf"
                  onChange={(e) => chooseFile(e.target.files?.[0] ?? null)}
                  disabled={busy}
                />
                <label className="admin-dropzone__hit" htmlFor="knowledge-doc-file">
                  <span className="admin-dropzone__title">
                    {file ? file.name : "Drop a PDF here"}
                  </span>
                  <span className="admin-dropzone__hint">
                    {file ? "Click to choose a different file" : "or click to browse"}
                  </span>
                </label>
              </div>

              <div className="form__field">
                <label className="form__label" htmlFor="knowledge-doc-title">
                  Title
                </label>
                <input
                  id="knowledge-doc-title"
                  className="form__input"
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="Defaults from the file name"
                  disabled={busy}
                />
              </div>
              <div className="admin-upload__row">
                <div className="form__field">
                  <label className="form__label" htmlFor="knowledge-doc-type">
                    Type
                  </label>
                  <select
                    id="knowledge-doc-type"
                    className="form__input"
                    value={docType}
                    onChange={(e) => setDocType(e.target.value)}
                    disabled={busy}
                  >
                    {DOC_TYPES.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>
                <fieldset className="form__field admin-role-checks">
                  <legend className="form__label">Roles</legend>
                  <div className="admin-role-checks__list">
                    {ROLE_OPTIONS.map((opt) => (
                      <label key={opt.value} className="admin-role-checks__item">
                        <input
                          type="checkbox"
                          name="required-roles"
                          value={opt.value}
                          checked={requiredRoles.includes(opt.value)}
                          disabled={busy}
                          onChange={() => {
                            setRequiredRoles((current) =>
                              current.includes(opt.value)
                                ? current.filter((role) => role !== opt.value)
                                : [...current, opt.value],
                            );
                          }}
                        />
                        {opt.label}
                      </label>
                    ))}
                  </div>
                </fieldset>
              </div>
              {uploadError && (
                <p className="form__error" role="alert">
                  {uploadError}
                </p>
              )}
              {reconnecting && (
                <p className="admin-upload__status" role="status">
                  Reconnecting…
                </p>
              )}
              {uploadStatus && (
                <p className="admin-upload__status" role="status">
                  Ingest: <IngestStatusBadge status={uploadStatus} />
                </p>
              )}
              <div className="admin-upload__actions">
                <PushButton
                  type="submit"
                  disabled={!file || busy || requiredRoles.length === 0}
                  loading={busy}
                >
                  {busy ? "Processing…" : "Upload PDF"}
                </PushButton>
              </div>
            </form>
          )}
        </section>

        <section className="panel admin-documents__list" aria-labelledby="docs-list-heading">
          <h2 id="docs-list-heading" className="admin-documents__section-title">
            Indexed documents
          </h2>
          {loading && (
            <p className="muted" role="status">
              Loading documents…
            </p>
          )}
          {listError && !isDevMockSession && (
            <p className="form__error" role="alert">
              {listError}
            </p>
          )}
          {!loading && !listError && docs && docs.length === 0 && (
            <p className="muted" role="status">
              No documents yet.
            </p>
          )}
          {!loading && !listError && docs && docs.length > 0 && (
            <ul className="list">
              {docs.map((doc, index) => {
                const latest = doc.latest_version;
                return (
                  <li key={doc.id} className="list-item">
                    <span className="list-item__num">{index + 1}</span>
                    <div>
                      <p className="list-item__title">{doc.title}</p>
                      <p className="list-item__meta">
                        {doc.doc_type}
                        {latest ? ` · v${latest.version_number}` : " · no versions"}
                        {latest && latest.chunk_count > 0 ? ` · ${latest.chunk_count} chunks` : ""}
                        {latest?.failure_reason ? ` · ${latest.failure_reason}` : ""}
                      </p>
                    </div>
                    {latest ? (
                      <IngestStatusBadge status={latest.lifecycle_status} />
                    ) : (
                      <span className="badge badge--locked">none</span>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
