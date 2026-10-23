import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProgressReconnectError } from "../../api/progress";
import type { ProgressEnvelope } from "../../api/progressEnvelope";
import { AdminDocumentsPage } from "./AdminDocumentsPage";

const authState = vi.hoisted(() => ({
  isDevMockSession: false,
}));

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({
    isDevMockSession: authState.isDevMockSession,
  }),
}));

vi.mock("../../api/adminIngest", () => ({
  listKnowledgeDocuments: vi.fn(),
  uploadKnowledgeDocument: vi.fn(),
  watchKnowledgeDocumentVersionStatus: vi.fn(),
  getKnowledgeDocumentVersionStatus: vi.fn(),
  isTerminalIngestStatus: (status: string) =>
    status === "ready" ||
    status === "failed" ||
    status === "published" ||
    status === "superseded" ||
    status === "archived",
}));

import {
  getKnowledgeDocumentVersionStatus,
  listKnowledgeDocuments,
  watchKnowledgeDocumentVersionStatus,
  uploadKnowledgeDocument,
} from "../../api/adminIngest";

describe("AdminDocumentsPage", () => {
  beforeEach(() => {
    authState.isDevMockSession = false;
    vi.mocked(listKnowledgeDocuments).mockReset();
    vi.mocked(uploadKnowledgeDocument).mockReset();
    vi.mocked(watchKnowledgeDocumentVersionStatus).mockReset();
    vi.mocked(getKnowledgeDocumentVersionStatus).mockReset();
    vi.mocked(listKnowledgeDocuments).mockResolvedValue([
      {
        id: "doc-1",
        title: "Attendance policy",
        slug: "attendance-policy",
        doc_type: "policy",
        status: "active",
        required_roles: ["administrator", "teacher"],
        latest_version: {
          id: "ver-1",
          version_number: 1,
          lifecycle_status: "ready",
          failure_reason: null,
          chunk_count: 12,
        },
      },
    ]);
  });

  it("lists documents and posts the upload form", async () => {
    const user = userEvent.setup();
    vi.mocked(uploadKnowledgeDocument).mockResolvedValue({
      document_id: "doc-2",
      version_id: "ver-2",
      version_number: 1,
      title: "Handbook",
      doc_type: "handbook",
      lifecycle_status: "processing",
      ingest_job_id: "job-2",
    });
    vi.mocked(watchKnowledgeDocumentVersionStatus).mockResolvedValue({
      id: "ver-2",
      document_id: "doc-2",
      version_number: 1,
      lifecycle_status: "ready",
      failure_reason: null,
      chunk_count: 4,
    });

    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AdminDocumentsPage />
      </MemoryRouter>,
    );

    expect(screen.queryByLabelText("Breadcrumb")).not.toBeInTheDocument();
    expect(screen.queryByText(/Administrator · knowledge ingest/)).not.toBeInTheDocument();
    expect(screen.getByText("Upload PDFs to index for Policy chat.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Upload document" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Indexed documents" })).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("Attendance policy")).toBeInTheDocument();
    });
    expect(screen.getByText("ready")).toBeInTheDocument();

    expect(screen.getByRole("checkbox", { name: "Administrator" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Teacher" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Student" })).not.toBeChecked();

    await user.clear(screen.getByLabelText(/^title$/i));
    await user.type(screen.getByLabelText(/^title$/i), "Handbook");
    await user.selectOptions(screen.getByLabelText(/^type$/i), "handbook");

    const file = new File(["%PDF-1.4"], "handbook.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("PDF file"), { target: { files: [file] } });
    await user.click(screen.getByRole("button", { name: /upload pdf/i }));

    await waitFor(() => {
      expect(uploadKnowledgeDocument).toHaveBeenCalled();
    });

    const [payload] = vi.mocked(uploadKnowledgeDocument).mock.calls[0]!;
    expect(payload.title).toBe("Handbook");
    expect(payload.docType).toBe("handbook");
    expect(payload.file).toBeInstanceOf(File);
    expect(payload.requiredRoles).toEqual(["administrator", "teacher"]);
    expect(watchKnowledgeDocumentVersionStatus).toHaveBeenCalledWith(
      "ver-2",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(listKnowledgeDocuments).toHaveBeenCalledTimes(2);
  });

  it("shows the worker failure reason after a failed ingest", async () => {
    const user = userEvent.setup();
    const reason =
      "Conversion failed for: hemh109.pdf with status: failure. — InvalidCxxCompiler: No working C++ compiler found";
    vi.mocked(uploadKnowledgeDocument).mockResolvedValue({
      document_id: "doc-2",
      version_id: "ver-2",
      version_number: 1,
      title: "Handbook",
      doc_type: "handbook",
      lifecycle_status: "processing",
      ingest_job_id: "job-2",
    });
    vi.mocked(watchKnowledgeDocumentVersionStatus).mockResolvedValue({
      id: "ver-2",
      document_id: "doc-2",
      version_number: 1,
      lifecycle_status: "failed",
      failure_reason: reason,
      chunk_count: 0,
    });

    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AdminDocumentsPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("Attendance policy")).toBeInTheDocument();
    });
    await user.type(screen.getByLabelText(/^title$/i), "Handbook");
    const file = new File(["%PDF-1.4"], "handbook.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("PDF file"), { target: { files: [file] } });
    await user.click(screen.getByRole("button", { name: /upload pdf/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("InvalidCxxCompiler");
    expect(screen.getByRole("alert")).toHaveTextContent("hemh109.pdf");
  });

  it("blocks upload for fixture mock sessions", async () => {
    authState.isDevMockSession = true;

    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AdminDocumentsPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/real administrator JWT/i);
    });
    expect(listKnowledgeDocuments).not.toHaveBeenCalled();
    expect(screen.queryByLabelText(/drop a pdf here/i)).not.toBeInTheDocument();
  });

  it("accepts a PDF dropped onto the upload zone", async () => {
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AdminDocumentsPage />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText("Attendance policy")).toBeInTheDocument();
    });

    const file = new File(["%PDF-1.4"], "fee_policy.pdf", { type: "application/pdf" });
    fireEvent.drop(screen.getByTestId("pdf-dropzone"), {
      dataTransfer: { files: [file] },
    });

    expect(screen.getByText("fee_policy.pdf")).toBeInTheDocument();
    expect(screen.getByLabelText(/^title$/i)).toHaveValue("fee policy");
  });

  async function startUpload() {
    const user = userEvent.setup();
    await waitFor(() => {
      expect(screen.getByText("Attendance policy")).toBeInTheDocument();
    });
    await user.type(screen.getByLabelText(/^title$/i), "Handbook");
    const file = new File(["%PDF-1.4"], "handbook.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("PDF file"), { target: { files: [file] } });
    await user.click(screen.getByRole("button", { name: /upload pdf/i }));
  }

  it("aborts the knowledge-document stream when the page unmounts", async () => {
    let signal: AbortSignal | undefined;
    vi.mocked(uploadKnowledgeDocument).mockResolvedValue({
      document_id: "doc-2",
      version_id: "ver-2",
      version_number: 1,
      title: "Handbook",
      doc_type: "handbook",
      lifecycle_status: "processing",
      ingest_job_id: "job-2",
    });
    vi.mocked(watchKnowledgeDocumentVersionStatus).mockImplementation((_versionId, options) => {
      signal = options?.signal;
      return new Promise((_resolve, reject) => {
        options?.signal?.addEventListener("abort", () => {
          reject(new DOMException("Aborted", "AbortError"));
        });
      });
    });

    const view = render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AdminDocumentsPage />
      </MemoryRouter>,
    );
    await startUpload();
    await waitFor(() => {
      expect(signal).toBeInstanceOf(AbortSignal);
    });
    expect(signal?.aborted).toBe(false);

    view.unmount();

    expect(signal?.aborted).toBe(true);
  });

  it("shows Reconnecting while a document ingest retries and clears it on the first frame", async () => {
    let reconnected: (() => void) | undefined;
    vi.mocked(uploadKnowledgeDocument).mockResolvedValue({
      document_id: "doc-2",
      version_id: "ver-2",
      version_number: 1,
      title: "Handbook",
      doc_type: "handbook",
      lifecycle_status: "processing",
      ingest_job_id: "job-2",
    });
    vi.mocked(watchKnowledgeDocumentVersionStatus).mockImplementation((_versionId, options) => {
      options?.onReconnecting?.({ attempt: 2, delayMs: 2_000 });
      reconnected = options?.onReconnected;
      return new Promise(() => undefined);
    });

    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AdminDocumentsPage />
      </MemoryRouter>,
    );
    await startUpload();

    expect(await screen.findByText("Reconnecting…", {}, { timeout: 3_000 })).toBeInTheDocument();
    act(() => {
      reconnected?.();
    });
    await waitFor(() => {
      expect(screen.queryByText("Reconnecting…")).not.toBeInTheDocument();
    });
  });

  it("shows Reconnecting and then the give-up error on a document upload", async () => {
    let fail: ((error: unknown) => void) | undefined;
    vi.mocked(uploadKnowledgeDocument).mockResolvedValue({
      document_id: "doc-2",
      version_id: "ver-2",
      version_number: 1,
      title: "Handbook",
      doc_type: "handbook",
      lifecycle_status: "processing",
      ingest_job_id: "job-2",
    });
    vi.mocked(watchKnowledgeDocumentVersionStatus).mockImplementation((_versionId, options) => {
      options?.onReconnecting?.({ attempt: 4, delayMs: 8_000 });
      return new Promise((_resolve, reject) => {
        fail = reject;
      });
    });

    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AdminDocumentsPage />
      </MemoryRouter>,
    );
    await startUpload();

    expect(await screen.findByText("Reconnecting…", {}, { timeout: 3_000 })).toBeInTheDocument();
    await act(async () => {
      fail?.(new ProgressReconnectError(8, 45_000, new TypeError("connection reset")));
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Lost the connection to progress updates. Try again.",
    );
    expect(screen.queryByText("Reconnecting…")).not.toBeInTheDocument();
  });

  it("retries a document progress stream and clears the give-up error", async () => {
    const user = userEvent.setup();
    let fail: ((error: unknown) => void) | undefined;
    let calls = 0;
    vi.mocked(uploadKnowledgeDocument).mockResolvedValue({
      document_id: "doc-2",
      version_id: "ver-2",
      version_number: 1,
      title: "Handbook",
      doc_type: "handbook",
      lifecycle_status: "processing",
      ingest_job_id: "job-2",
    });
    vi.mocked(getKnowledgeDocumentVersionStatus).mockResolvedValue({
      id: "ver-2",
      document_id: "doc-2",
      version_number: 1,
      lifecycle_status: "processing",
      failure_reason: null,
      chunk_count: 1,
    });
    vi.mocked(watchKnowledgeDocumentVersionStatus).mockImplementation(() => {
      calls += 1;
      if (calls === 1) {
        return new Promise((_resolve, reject) => {
          fail = reject;
        });
      }
      return Promise.resolve({
        id: "ver-2",
        document_id: "doc-2",
        version_number: 1,
        lifecycle_status: "ready",
        failure_reason: null,
        chunk_count: 4,
      });
    });

    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AdminDocumentsPage />
      </MemoryRouter>,
    );
    const slot = screen.getByTestId("progress-reconnect");
    expect(slot).toHaveAttribute("data-reserve", "fixed");
    await startUpload();
    await act(async () => {
      fail?.(new ProgressReconnectError(8, 45_000, new TypeError("connection reset")));
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Lost the connection to progress updates. Try again.",
    );

    await user.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => {
      expect(watchKnowledgeDocumentVersionStatus).toHaveBeenCalledTimes(2);
    });
    expect(getKnowledgeDocumentVersionStatus).toHaveBeenCalledWith("ver-2");
    expect(screen.queryByText("Lost the connection to progress updates. Try again.")).not.toBeInTheDocument();
    expect(screen.getByTestId("progress-reconnect")).toBe(slot);
    expect(slot).toHaveAttribute("data-active", "false");
  });

  it("opens the file picker from a click and from the keyboard", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AdminDocumentsPage />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByText("Attendance policy")).toBeInTheDocument();
    });

    const input = screen.getByLabelText("PDF file");
    const open = vi.spyOn(input, "click").mockImplementation(() => undefined);
    const browse = screen.getByRole("button", { name: /drop a pdf here/i });
    await user.click(browse);
    expect(open).toHaveBeenCalledTimes(1);
    browse.focus();
    await user.keyboard("{Enter}");
    expect(open).toHaveBeenCalledTimes(2);
    await user.keyboard(" ");
    expect(open).toHaveBeenCalledTimes(3);

    const file = new File(["%PDF-1.4"], "fee_policy.pdf", { type: "application/pdf" });
    fireEvent.drop(screen.getByTestId("pdf-dropzone"), {
      dataTransfer: { files: [file] },
    });
    expect(screen.getByText("fee_policy.pdf")).toBeInTheDocument();
  });

  it("does not apply the document list when the page unmounts during refresh", async () => {
    let resolveRefresh: ((docs: never[]) => void) | undefined;
    let calls = 0;
    let refreshSignal: AbortSignal | undefined;
    vi.mocked(listKnowledgeDocuments).mockImplementation((signal?: AbortSignal) => {
      calls += 1;
      if (calls === 1) {
        return Promise.resolve([
          {
            id: "doc-1",
            title: "Attendance policy",
            slug: "attendance-policy",
            doc_type: "policy",
            status: "active",
            required_roles: ["administrator", "teacher"],
            latest_version: {
              id: "ver-1",
              version_number: 1,
              lifecycle_status: "ready",
              failure_reason: null,
              chunk_count: 12,
            },
          },
        ]);
      }
      refreshSignal = signal;
      return new Promise((resolve) => {
        resolveRefresh = resolve;
      });
    });
    vi.mocked(uploadKnowledgeDocument).mockResolvedValue({
      document_id: "doc-2",
      version_id: "ver-2",
      version_number: 1,
      title: "Handbook",
      doc_type: "handbook",
      lifecycle_status: "processing",
      ingest_job_id: "job-2",
    });
    vi.mocked(watchKnowledgeDocumentVersionStatus).mockResolvedValue({
      id: "ver-2",
      document_id: "doc-2",
      version_number: 1,
      lifecycle_status: "ready",
      failure_reason: null,
      chunk_count: 3,
    });

    const view = render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AdminDocumentsPage />
      </MemoryRouter>,
    );
    await startUpload();
    await waitFor(() => {
      expect(calls).toBe(2);
    });

    expect(refreshSignal).toBeInstanceOf(AbortSignal);
    expect(refreshSignal?.aborted).toBe(false);

    view.unmount();
    expect(refreshSignal?.aborted).toBe(true);
    await act(async () => {
      resolveRefresh?.([]);
    });

    expect(calls).toBe(2);
  });

  it("shows 16 of 64 chunks from an embedding progress frame", async () => {
    vi.mocked(uploadKnowledgeDocument).mockResolvedValue({
      document_id: "doc-2",
      version_id: "ver-2",
      version_number: 1,
      title: "Handbook",
      doc_type: "handbook",
      lifecycle_status: "processing",
      ingest_job_id: "job-2",
    });
    vi.mocked(watchKnowledgeDocumentVersionStatus).mockImplementation((_versionId, options) => {
      options?.onProgress?.(embeddingProgress("ver-2"));
      return new Promise(() => undefined);
    });

    render(
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AdminDocumentsPage />
      </MemoryRouter>,
    );
    await startUpload();

    expect(await screen.findByText("16 of 64 chunks")).toBeInTheDocument();
    const meter = screen.getByRole("progressbar", { name: "16 of 64 chunks" });
    expect(meter).toHaveAttribute("value", "16");
    expect(meter).toHaveAttribute("max", "64");
  });
});

function embeddingProgress(subjectId: string): ProgressEnvelope {
  return {
    v: 1,
    kind: "upload",
    subject_id: subjectId,
    seq: 4,
    stage: "embedding",
    status: "running",
    percent: 60,
    current: 16,
    total: 64,
    unit: "chunk",
    message: "Embedding 16 of 64 chunks.",
    updated_at: "2026-10-20T10:15:03+00:00",
  };
}
