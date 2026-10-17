import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
}));

import {
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
    fireEvent.change(screen.getByLabelText(/drop a pdf here/i), { target: { files: [file] } });
    await user.click(screen.getByRole("button", { name: /upload pdf/i }));

    await waitFor(() => {
      expect(uploadKnowledgeDocument).toHaveBeenCalled();
    });

    const [payload] = vi.mocked(uploadKnowledgeDocument).mock.calls[0]!;
    expect(payload.title).toBe("Handbook");
    expect(payload.docType).toBe("handbook");
    expect(payload.file).toBeInstanceOf(File);
    expect(payload.requiredRoles).toEqual(["administrator", "teacher"]);
    expect(watchKnowledgeDocumentVersionStatus).toHaveBeenCalledWith("ver-2");
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
    fireEvent.change(screen.getByLabelText(/drop a pdf here/i), { target: { files: [file] } });
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
});
