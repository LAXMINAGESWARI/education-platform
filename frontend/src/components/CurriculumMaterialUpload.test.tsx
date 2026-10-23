import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProgressReconnectError } from "../api/progress";
import type { ProgressEnvelope } from "../api/progressEnvelope";
import { CurriculumMaterialUpload } from "./CurriculumMaterialUpload";

vi.mock("../api/adminIngest", () => ({
  uploadSubtopicMaterial: vi.fn(),
  watchMaterialVersionStatus: vi.fn(),
  getMaterialVersionStatus: vi.fn(),
  isTerminalIngestStatus: (status: string) =>
    status === "ready" ||
    status === "failed" ||
    status === "published" ||
    status === "superseded" ||
    status === "archived",
}));

import {
  getMaterialVersionStatus,
  uploadSubtopicMaterial,
  watchMaterialVersionStatus,
} from "../api/adminIngest";

describe("CurriculumMaterialUpload", () => {
  beforeEach(() => {
    vi.mocked(uploadSubtopicMaterial).mockReset();
    vi.mocked(watchMaterialVersionStatus).mockReset();
    vi.mocked(getMaterialVersionStatus).mockReset();
    vi.mocked(uploadSubtopicMaterial).mockResolvedValue({
      source_material_id: "mat-1",
      version_id: "ver-1",
      version_number: 1,
      title: "Place value",
      lifecycle_status: "processing",
      ingest_job_id: "job-1",
    });
  });

  async function submitPdf() {
    const user = userEvent.setup();
    const file = new File(["%PDF-1.4"], "lesson.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText(/pdf file/i), { target: { files: [file] } });
    await user.click(screen.getByRole("button", { name: /upload pdf/i }));
  }

  it("aborts the material stream when the form unmounts", async () => {
    let signal: AbortSignal | undefined;
    vi.mocked(watchMaterialVersionStatus).mockImplementation((_versionId, options) => {
      signal = options?.signal;
      return new Promise((_resolve, reject) => {
        options?.signal?.addEventListener("abort", () => {
          reject(new DOMException("Aborted", "AbortError"));
        });
      });
    });

    const view = render(<CurriculumMaterialUpload subtopicId="st-1" />);
    await submitPdf();
    await waitFor(() => {
      expect(signal).toBeInstanceOf(AbortSignal);
    });

    view.unmount();

    expect(signal?.aborted).toBe(true);
  });

  it("shows Reconnecting while the material stream retries and clears it on the first frame", async () => {
    let reconnected: (() => void) | undefined;
    let reconnect: (() => void) | undefined;
    window.matchMedia = ((query: string) => ({
      matches: query === "(prefers-reduced-motion: reduce)",
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })) as unknown as typeof window.matchMedia;
    vi.mocked(watchMaterialVersionStatus).mockImplementation((_versionId, options) => {
      reconnect = () => options?.onReconnecting?.({ attempt: 1, delayMs: 1_000 });
      reconnected = options?.onReconnected;
      return new Promise(() => undefined);
    });

    render(<CurriculumMaterialUpload subtopicId="st-1" />);
    const slot = screen.getByTestId("progress-reconnect");
    expect(slot).toHaveAttribute("data-reserve", "fixed");
    expect(slot).toHaveAttribute("data-active", "false");
    await submitPdf();
    expect(slot).toHaveAttribute("data-active", "false");

    act(() => {
      reconnect?.();
    });

    expect(await screen.findByText("Reconnecting…", {}, { timeout: 3_000 })).toBeInTheDocument();
    expect(slot).toHaveAttribute("data-active", "true");
    expect(within(slot).getByRole("status")).toHaveAttribute("aria-live", "polite");
    expect(slot.querySelector("[data-motion='reduced']")).not.toBeNull();
    expect(slot.querySelector(".progress-reconnect__spinner")).toBeNull();
    act(() => {
      reconnected?.();
    });
    await waitFor(() => {
      expect(screen.queryByText("Reconnecting…")).not.toBeInTheDocument();
    });
    expect(slot).toHaveAttribute("data-active", "false");
    expect(screen.getByTestId("progress-reconnect")).toBe(slot);
  });

  it("shows Reconnecting and then the give-up error", async () => {
    let fail: ((error: unknown) => void) | undefined;
    vi.mocked(watchMaterialVersionStatus).mockImplementation((_versionId, options) => {
      options?.onReconnecting?.({ attempt: 4, delayMs: 8_000 });
      return new Promise((_resolve, reject) => {
        fail = reject;
      });
    });

    render(<CurriculumMaterialUpload subtopicId="st-1" />);
    await submitPdf();

    expect(await screen.findByText("Reconnecting…", {}, { timeout: 3_000 })).toBeInTheDocument();
    await act(async () => {
      fail?.(new ProgressReconnectError(8, 45_000, new TypeError("connection reset")));
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Lost the connection to progress updates. Try again.",
    );
    expect(screen.queryByText("Reconnecting…")).not.toBeInTheDocument();
  });

  it("keeps a reserved reconnect row and retries the material stream", async () => {
    const user = userEvent.setup();
    let fail: ((error: unknown) => void) | undefined;
    let calls = 0;
    vi.mocked(getMaterialVersionStatus).mockResolvedValue({
      id: "ver-1",
      source_material_id: "mat-1",
      version_number: 1,
      title: "Place value",
      lifecycle_status: "processing",
      failure_reason: null,
      chunk_count: 2,
    });
    vi.mocked(watchMaterialVersionStatus).mockImplementation(() => {
      calls += 1;
      if (calls === 1) {
        return new Promise((_resolve, reject) => {
          fail = reject;
        });
      }
      return Promise.resolve({
        id: "ver-1",
        source_material_id: "mat-1",
        version_number: 1,
        title: "Place value",
        lifecycle_status: "ready",
        failure_reason: null,
        chunk_count: 4,
      });
    });

    render(<CurriculumMaterialUpload subtopicId="st-1" />);
    const slot = screen.getByTestId("progress-reconnect");
    await submitPdf();
    await act(async () => {
      fail?.(new ProgressReconnectError(8, 45_000, new TypeError("connection reset")));
    });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Lost the connection to progress updates. Try again.");
    expect(slot).toHaveAttribute("data-active", "false");

    await user.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => {
      expect(watchMaterialVersionStatus).toHaveBeenCalledTimes(2);
    });
    expect(getMaterialVersionStatus).toHaveBeenCalledWith("ver-1");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("ready")).toBeInTheDocument();
    expect(screen.getByTestId("progress-reconnect")).toBe(slot);
    expect(screen.getByLabelText(/pdf file/i)).toHaveProperty("files", expect.anything());
    const input = screen.getByLabelText(/pdf file/i) as HTMLInputElement;
    expect(input.files?.[0]?.name).toBe("lesson.pdf");
    expect(screen.getByRole("button", { name: /upload pdf/i })).toBeEnabled();
  });

  it("does not watch a material version after the form unmounts", async () => {
    const user = userEvent.setup();
    let resolveUpload: (value: {
      source_material_id: string;
      version_id: string;
      version_number: number;
      title: string;
      lifecycle_status: "processing";
      ingest_job_id: string;
    }) => void = () => undefined;
    vi.mocked(uploadSubtopicMaterial).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveUpload = resolve;
        }),
    );

    const view = render(<CurriculumMaterialUpload subtopicId="st-1" />);
    const file = new File(["%PDF-1.4"], "lesson.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText(/pdf file/i), { target: { files: [file] } });
    await user.click(screen.getByRole("button", { name: /upload pdf/i }));
    await waitFor(() => {
      expect(uploadSubtopicMaterial).toHaveBeenCalled();
    });

    view.unmount();
    await act(async () => {
      resolveUpload({
        source_material_id: "mat-1",
        version_id: "ver-1",
        version_number: 1,
        title: "Place value",
        lifecycle_status: "processing",
        ingest_job_id: "job-1",
      });
    });

    expect(watchMaterialVersionStatus).not.toHaveBeenCalled();
  });

  it("shows 16 of 64 chunks from an embedding progress frame", async () => {
    vi.mocked(watchMaterialVersionStatus).mockImplementation((_versionId, options) => {
      options?.onProgress?.(embeddingProgress("ver-1"));
      return new Promise(() => undefined);
    });

    render(<CurriculumMaterialUpload subtopicId="st-1" />);
    await submitPdf();

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
