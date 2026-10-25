import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type FeedbackOverview } from "../api/types";
import { FeedbackSubjectPickerPage } from "./FeedbackSubjectPickerPage";
import { HomePage, clearLearningDirectoryCache } from "./HomePage";

vi.mock("../api/feedback", () => ({
  getFeedbackOverview: vi.fn(),
  getFeedbackHighlights: vi.fn(),
  getFeedbackSubject: vi.fn().mockResolvedValue({ topics_needing_attention: [] }),
}));

vi.mock("../api/materials", () => ({
  fetchLearningDirectory: vi.fn(),
}));

import { getFeedbackHighlights, getFeedbackOverview } from "../api/feedback";
import { fetchLearningDirectory } from "../api/materials";

describe("Feedback and Subjects empty states", () => {
  beforeEach(() => {
    clearLearningDirectoryCache();
    vi.mocked(fetchLearningDirectory).mockReset();
    vi.mocked(getFeedbackOverview).mockReset();
    vi.mocked(getFeedbackHighlights).mockReset();
  });

  it("renders the Subjects page with empty enrollment data", async () => {
    vi.mocked(fetchLearningDirectory).mockResolvedValue({ subjects: [] });

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "Your subjects" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Science" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "English" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Social Studies" })).toBeInTheDocument();
    expect(screen.getAllByText("Coming soon").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Locked").length).toBeGreaterThan(0);
    expect(
      screen.queryByText("No enrolled subjects with published curriculum yet."),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Subject progress" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders a subject page when that subject has no topics", async () => {
    vi.mocked(fetchLearningDirectory).mockResolvedValue({
      subjects: [
        {
          id: "math-1",
          code: "MATH",
          name: "Mathematics",
          grade_name: "Grade 8",
          academic_period_name: "2026-27",
          progress_percent: 0,
          topics: [],
        },
      ],
    });

    render(
      <MemoryRouter initialEntries={["/subjects/math-1"]}>
        <Routes>
          <Route path="/subjects/:subjectId" element={<HomePage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "Mathematics" })).toBeInTheDocument();
    expect(screen.getByText("No school material published yet.")).toBeInTheDocument();
    expect(screen.getByText("Coming soon")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Topics" })).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("renders the Feedback page with empty enrollment data", async () => {
    vi.mocked(getFeedbackOverview).mockResolvedValue({ score_percent: null, subjects: [] });
    vi.mocked(getFeedbackHighlights).mockResolvedValue({ due_reviews: [], trajectories: [] });
    vi.mocked(fetchLearningDirectory).mockResolvedValue({ subjects: [] });

    render(
      <MemoryRouter>
        <FeedbackSubjectPickerPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "Feedback" })).toBeInTheDocument();
    expect(await screen.findByText("No enrolled subjects yet.")).toBeInTheDocument();
    const summary = screen.getByRole("region", { name: "Across all subjects" });
    expect(summary).toHaveTextContent("Not attempted");
    expect(summary).toHaveTextContent("Concepts mastered out of those you've tried");
    expect(summary).not.toHaveTextContent("Concepts you have already tried");
    expect(screen.getByRole("region", { name: "Across all subjects" })).not.toHaveTextContent("0%");
    expect(screen.queryByRole("table", { name: "Feedback by subject" })).not.toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Due for review" })).not.toBeInTheDocument();
  });

  it("shows a loading status and then an error alert on the Subjects page", async () => {
    vi.mocked(fetchLearningDirectory).mockReturnValue(new Promise(() => undefined));
    const { unmount } = render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(screen.getByRole("status")).toHaveTextContent("Loading subjects…");
    unmount();

    clearLearningDirectoryCache();
    vi.mocked(fetchLearningDirectory).mockRejectedValue(new ApiError("Could not load subjects.", 500, null));
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load subjects.");
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Science" })).toBeInTheDocument();
  });

  it("keeps a subject route on its own loading and error states", async () => {
    vi.mocked(fetchLearningDirectory).mockReturnValue(new Promise(() => undefined));
    const { unmount } = render(
      <MemoryRouter initialEntries={["/subjects/math-1"]}>
        <Routes>
          <Route path="/subjects/:subjectId" element={<HomePage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole("status")).toHaveTextContent("Loading subject…");
    expect(screen.queryByRole("heading", { name: "Science" })).not.toBeInTheDocument();
    unmount();

    clearLearningDirectoryCache();
    vi.mocked(fetchLearningDirectory).mockRejectedValue(new ApiError("Could not load subjects.", 500, null));
    render(
      <MemoryRouter initialEntries={["/subjects/math-1"]}>
        <Routes>
          <Route path="/subjects/:subjectId" element={<HomePage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load subjects.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to subjects" })).toHaveAttribute("href", "/");
    expect(screen.queryByRole("heading", { name: "Science" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Your subjects" })).not.toBeInTheDocument();
  });

  it("shows feedback loading and clears the error when Try again starts", async () => {
    vi.mocked(getFeedbackHighlights).mockResolvedValue({ due_reviews: [], trajectories: [] });
    vi.mocked(fetchLearningDirectory).mockResolvedValue({ subjects: [] });
    vi.mocked(getFeedbackOverview).mockReturnValue(new Promise(() => undefined));
    const { unmount } = render(
      <MemoryRouter>
        <FeedbackSubjectPickerPage />
      </MemoryRouter>,
    );

    expect(screen.getByRole("status")).toHaveTextContent("Loading feedback…");
    unmount();

    vi.mocked(getFeedbackOverview).mockRejectedValueOnce(
      new ApiError("Could not load feedback.", 500, null),
    );
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <FeedbackSubjectPickerPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load feedback.");

    let resolveOverview: (value: FeedbackOverview | PromiseLike<FeedbackOverview>) => void = () =>
      undefined;
    vi.mocked(getFeedbackOverview).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveOverview = resolve;
        }),
    );
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading feedback…");

    resolveOverview({ score_percent: null, subjects: [] });
    expect(await screen.findByText("No enrolled subjects yet.")).toBeInTheDocument();
  });
});
