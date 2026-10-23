import { apiRequest } from "./client";
import { versionSubject, watchProgress, type ProgressReconnectNotice } from "./progress";
import type { ProgressEnvelope } from "./progressEnvelope";
import type {
  IngestLifecycleStatus,
  KnowledgeDocumentAccepted,
  KnowledgeDocumentDetail,
  KnowledgeDocumentListItem,
  KnowledgeDocumentVersionStatus,
  MaterialIngestAccepted,
  MaterialVersionStatus,
} from "./types";

const TERMINAL_STATUSES = new Set<IngestLifecycleStatus>([
  "ready",
  "failed",
  "published",
  "superseded",
  "archived",
]);

export function isTerminalIngestStatus(status: IngestLifecycleStatus): boolean {
  return TERMINAL_STATUSES.has(status);
}

export async function uploadSubtopicMaterial(
  subtopicId: string,
  file: File,
  title: string,
): Promise<MaterialIngestAccepted> {
  const body = new FormData();
  body.append("file", file);
  body.append("title", title);
  return apiRequest<MaterialIngestAccepted>(
    `/admin/subtopics/${encodeURIComponent(subtopicId)}/materials`,
    { method: "POST", body },
  );
}

export async function getMaterialVersionStatus(
  versionId: string,
): Promise<MaterialVersionStatus> {
  return apiRequest<MaterialVersionStatus>(
    `/admin/material-versions/${encodeURIComponent(versionId)}`,
  );
}

export type StreamWatchOptions = {
  signal?: AbortSignal;
  onProgress?: (progress: ProgressEnvelope) => void;
  onReconnecting?: (notice: ProgressReconnectNotice) => void;
  /** Fires on the first frame of a connection, including the first connect. */
  onReconnected?: () => void;
};

export async function watchMaterialVersionStatus(
  versionId: string,
  options: StreamWatchOptions = {},
): Promise<MaterialVersionStatus> {
  const snapshot = await watchProgress(versionSubject(versionId), {
    signal: options.signal,
    onProgress: options.onProgress,
    onReconnecting: options.onReconnecting,
    onReconnected: options.onReconnected,
  });
  return snapshot as MaterialVersionStatus;
}

export type KnowledgeDocumentUploadInput = {
  file: File;
  title: string;
  docType: string;
  requiredRoles?: string[];
};

export async function uploadKnowledgeDocument(
  input: KnowledgeDocumentUploadInput,
): Promise<KnowledgeDocumentAccepted> {
  const body = new FormData();
  body.append("file", input.file);
  body.append("title", input.title);
  body.append("doc_type", input.docType);
  if (input.requiredRoles && input.requiredRoles.length > 0) {
    body.append("required_roles", input.requiredRoles.join(","));
  }
  return apiRequest<KnowledgeDocumentAccepted>("/admin/knowledge-documents", {
    method: "POST",
    body,
  });
}

export async function listKnowledgeDocuments(
  signal?: AbortSignal,
): Promise<KnowledgeDocumentListItem[]> {
  const data = await apiRequest<
    KnowledgeDocumentListItem[] | { items: KnowledgeDocumentListItem[] }
  >("/admin/knowledge-documents", signal === undefined ? undefined : { signal });
  return Array.isArray(data) ? data : data.items;
}

export async function getKnowledgeDocument(
  documentId: string,
): Promise<KnowledgeDocumentDetail> {
  return apiRequest<KnowledgeDocumentDetail>(
    `/admin/knowledge-documents/${encodeURIComponent(documentId)}`,
  );
}

export async function getKnowledgeDocumentVersionStatus(
  versionId: string,
): Promise<KnowledgeDocumentVersionStatus> {
  return apiRequest<KnowledgeDocumentVersionStatus>(
    `/admin/knowledge-document-versions/${encodeURIComponent(versionId)}`,
  );
}

export async function watchKnowledgeDocumentVersionStatus(
  versionId: string,
  options: StreamWatchOptions = {},
): Promise<KnowledgeDocumentVersionStatus> {
  const snapshot = await watchProgress(versionSubject(versionId), {
    signal: options.signal,
    onProgress: options.onProgress,
    onReconnecting: options.onReconnecting,
    onReconnected: options.onReconnected,
  });
  return snapshot as KnowledgeDocumentVersionStatus;
}
