import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProgressReconnectError } from "../api/progress";
import { CurriculumMaterialUpload } from "./CurriculumMaterialUpload";

vi.mock("../api/adminIngest", () => ({
  uploadSubtopicMaterial: vi.fn(),
  watchMaterialVersionStatus: vi.fn(),
}));

import { uploadSubtopicMaterial, watchMaterialVersionStatus } from "../api/adminIngest";

describe("CurriculumMaterialUpload", () => {
  beforeEach(() => {
    vi.mocked(uploadSubtopicMaterial).mockReset();
    vi.mocked(watchMaterialVersionStatus).mockReset();
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

  it("shows Reconnecting while the material stream retries and clears it when the socket returns", async () => {
    let reconnected: (() => void) | undefined;
    vi.mocked(watchMaterialVersionStatus).mockImplementation((_versionId, options) => {
      options?.onReconnecting?.({ attempt: 1, delayMs: 1_000 });
      reconnected = options?.onReconnected;
      return new Promise(() => undefined);
    });

    render(<CurriculumMaterialUpload subtopicId="st-1" />);
    await submitPdf();

    expect(await screen.findByText("Reconnecting…")).toBeInTheDocument();
    act(() => {
      reconnected?.();
    });
    await waitFor(() => {
      expect(screen.queryByText("Reconnecting…")).not.toBeInTheDocument();
    });
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

    expect(await screen.findByText("Reconnecting…")).toBeInTheDocument();
    await act(async () => {
      fail?.(new ProgressReconnectError(8, 45_000, new TypeError("connection reset")));
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Lost the connection to progress updates. Try again.",
    );
    expect(screen.queryByText("Reconnecting…")).not.toBeInTheDocument();
  });
});
