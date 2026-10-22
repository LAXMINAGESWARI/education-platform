import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./client", () => ({
  apiRequest: vi.fn(),
}));

import { apiRequest } from "./client";
import {
  isTerminalIngestStatus,
  listKnowledgeDocuments,
  uploadKnowledgeDocument,
  uploadSubtopicMaterial,
} from "./adminIngest";

describe("adminIngest helpers", () => {
  beforeEach(() => {
    vi.mocked(apiRequest).mockReset();
  });

  it("marks ready and failed as terminal", () => {
    expect(isTerminalIngestStatus("ready")).toBe(true);
    expect(isTerminalIngestStatus("failed")).toBe(true);
    expect(isTerminalIngestStatus("processing")).toBe(false);
  });

  it("posts curriculum material as FormData", async () => {
    vi.mocked(apiRequest).mockResolvedValue({
      source_material_id: "mat-1",
      version_id: "ver-1",
      version_number: 1,
      title: "Place value",
      lifecycle_status: "processing",
      ingest_job_id: "job-1",
    });

    const file = new File(["%PDF"], "lesson.pdf", { type: "application/pdf" });
    await uploadSubtopicMaterial("st-1", file, "Place value");

    expect(apiRequest).toHaveBeenCalledTimes(1);
    const [path, options] = vi.mocked(apiRequest).mock.calls[0]!;
    expect(path).toBe("/admin/subtopics/st-1/materials");
    expect(options?.method).toBe("POST");
    expect(options?.body).toBeInstanceOf(FormData);
    const body = options!.body as FormData;
    expect(body.get("title")).toBe("Place value");
    expect(body.get("file")).toBeInstanceOf(File);
  });

  it("posts knowledge documents as FormData with roles", async () => {
    vi.mocked(apiRequest).mockResolvedValue({
      document_id: "doc-1",
      version_id: "ver-1",
      version_number: 1,
      title: "Attendance",
      doc_type: "policy",
      lifecycle_status: "processing",
      ingest_job_id: "job-1",
    });

    const file = new File(["%PDF"], "policy.pdf", { type: "application/pdf" });
    await uploadKnowledgeDocument({
      file,
      title: "Attendance",
      docType: "policy",
      requiredRoles: ["administrator", "teacher"],
    });

    const [path, options] = vi.mocked(apiRequest).mock.calls[0]!;
    expect(path).toBe("/admin/knowledge-documents");
    expect(options?.method).toBe("POST");
    const body = options!.body as FormData;
    expect(body.get("title")).toBe("Attendance");
    expect(body.get("doc_type")).toBe("policy");
    expect(body.get("required_roles")).toBe("administrator,teacher");
  });

  it("normalizes list responses that wrap items", async () => {
    vi.mocked(apiRequest).mockResolvedValue({
      items: [
        {
          id: "doc-1",
          title: "Handbook",
          slug: "handbook",
          doc_type: "handbook",
          status: "active",
          required_roles: ["administrator"],
          latest_version: null,
        },
      ],
    });

    const docs = await listKnowledgeDocuments();
    expect(docs).toHaveLength(1);
    expect(docs[0]?.title).toBe("Handbook");
    expect(apiRequest).toHaveBeenCalledWith("/admin/knowledge-documents", undefined);
  });

  it("forwards an abort signal when listing knowledge documents", async () => {
    vi.mocked(apiRequest).mockResolvedValue([]);
    const abort = new AbortController();
    await listKnowledgeDocuments(abort.signal);
    expect(apiRequest).toHaveBeenCalledWith("/admin/knowledge-documents", { signal: abort.signal });
  });
});
