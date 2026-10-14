import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AtRiskFlag } from "../../api/atRisk";
import type { DashboardSummary } from "../../api/insights";
import type { LearningDirectory } from "../../api/types";
import { AdminDashboardPage } from "./AdminDashboardPage";

vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ isDevMockSession: false }),
}));

vi.mock("../../api/insights", () => ({
  fetchDashboardSummary: vi.fn(),
}));

vi.mock("../../api/atRisk", () => ({
  fetchAtRiskFlags: vi.fn(),
}));

vi.mock("../../api/materials", () => ({
  fetchLearningDirectory: vi.fn(),
}));

import { fetchAtRiskFlags } from "../../api/atRisk";
import { fetchDashboardSummary } from "../../api/insights";
import { fetchLearningDirectory } from "../../api/materials";

const summary: DashboardSummary = {
  scope_description: "Whole institution",
  total_students: 42,
  average_attendance: 91.24,
  average_mastery: 63.5,
  subjects: [
    {
      grade: "Grade 8",
      subject: "Science",
      students: 20,
      students_attempted: 12,
      average_mastery: 48.2,
      quizzes_taken: 30,
      quizzes_passed: 9,
    },
    {
      grade: "Grade 8",
      subject: "Mathematics",
      students: 22,
      students_attempted: 20,
      average_mastery: 61,
      quizzes_taken: 40,
      quizzes_passed: 30,
    },
    {
      grade: "Grade 9",
      subject: "Music",
      students: 18,
      students_attempted: 3,
      average_mastery: 22,
      quizzes_taken: 3,
      quizzes_passed: 0,
    },
    {
      grade: "Grade 9",
      subject: "Art",
      students: 15,
      students_attempted: 0,
      average_mastery: null,
      quizzes_taken: 0,
      quizzes_passed: 0,
    },
  ],
};

const directory: LearningDirectory = {
  subjects: [
    {
      id: "subj-math",
      code: "MATH",
      name: "Mathematics",
      grade_name: "Grade 8",
      academic_period_name: "2026-2027",
      progress_percent: 0,
      topics: [
        {
          id: "topic-1",
          title: "Numbers",
          slug: "numbers",
          sequence: 1,
          progress_percent: 0,
          complete: false,
          objectives: [],
          subtopics: [
            {
              id: "st-1",
              title: "Place value",
              slug: "place-value",
              sequence: 1,
              has_lesson: true,
              lesson_completed: false,
              progress_percent: 0,
              quiz: {
                id: "quiz-1",
                title: "Place value quiz",
                scope: "subtopic_mastery",
                available: true,
                unlocked: true,
                locked_reason: null,
                pass_threshold_percent: 70,
                attempt_count: 0,
                best_score_percent: null,
                passed: false,
                in_progress_attempt_id: null,
                recent_attempts: [],
              },
            },
          ],
          overall_quiz: null,
        },
      ],
    },
    {
      id: "subj-sci",
      code: "SCI",
      name: "Science",
      grade_name: "Grade 8",
      academic_period_name: "2026-2027",
      progress_percent: 0,
      topics: [],
    },
  ],
};

function flag(id: string, studentId: string, tier: AtRiskFlag["tier"]): AtRiskFlag {
  return {
    id,
    student_id: studentId,
    student_name: studentId,
    grade_subject_offering_id: null,
    subject: null,
    tier,
    drivers: [],
    status: "active",
    dismissed_by_user_id: null,
    dismissal_note: null,
  };
}

function renderPage() {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AdminDashboardPage />
    </MemoryRouter>,
  );
}

describe("AdminDashboardPage", () => {
  beforeEach(() => {
    vi.mocked(fetchDashboardSummary).mockReset().mockResolvedValue(summary);
    vi.mocked(fetchAtRiskFlags)
      .mockReset()
      .mockResolvedValue({
        rows_returned: 3,
        items: [flag("f1", "s1", "urgent"), flag("f2", "s1", "monitor"), flag("f3", "s2", "urgent")],
      });
    vi.mocked(fetchLearningDirectory).mockReset().mockResolvedValue(directory);
  });

  it("shows the school's headline numbers", async () => {
    renderPage();
    expect(await screen.findByText("42")).toBeInTheDocument();
    expect(screen.getByText("91.2%")).toBeInTheDocument();
    expect(screen.getByText("63.5%")).toBeInTheDocument();
  });

  it("counts flagged students, not just flags, and links to the at-risk page", async () => {
    renderPage();
    expect(await screen.findByText("2 students")).toBeInTheDocument();
    expect(screen.getByText("2 urgent")).toBeInTheDocument();
    expect(screen.getByText("1 monitor")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Review at-risk flags/ })).toHaveAttribute(
      "href",
      "/admin/at-risk",
    );
  });

  it("lists the weakest subjects first and leaves out ones nobody has attempted", async () => {
    renderPage();
    expect(await screen.findByText("Subjects to look into")).toBeInTheDocument();
    const science = await screen.findByText("Grade 8 · Science");
    const maths = screen.getByText("Grade 8 · Mathematics");
    expect(science.compareDocumentPosition(maths) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByText("Grade 9 · Art")).not.toBeInTheDocument();
    expect(screen.getByText(/30% of attempts passed/)).toBeInTheDocument();
  });

  it("hides subjects with fewer than 5 scored students and says so", async () => {
    renderPage();
    await screen.findByText("Grade 8 · Science");
    expect(screen.queryByText("Grade 9 · Music")).not.toBeInTheDocument();
    expect(
      screen.getByText("1 subject not shown yet: fewer than 5 students have taken a quiz."),
    ).toBeInTheDocument();
  });

  it("marks low participation when under 80% of students took a quiz", async () => {
    renderPage();
    await screen.findByText("Grade 8 · Science");
    // Science: 12 of 20 attempted (60%). Mathematics: 20 of 22 (91%).
    expect(screen.getAllByText("Low participation")).toHaveLength(1);
  });

  it("explains when no subject has enough results to compare", async () => {
    vi.mocked(fetchDashboardSummary).mockResolvedValue({ ...summary, subjects: [] });
    renderPage();
    expect(
      await screen.findByText(/No subject has quiz results from at least 5 students yet/),
    ).toBeInTheDocument();
  });

  it("highlights subjects with no published content in the coverage table", async () => {
    renderPage();
    expect(await screen.findByText("None yet")).toBeInTheDocument();
    expect(screen.getByText("1 without content")).toBeInTheDocument();
    expect(
      screen.getByText("1 of 2 subjects have no published topics yet. 1 topic published in total."),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Grade 8 Science: no content yet" })).toHaveAttribute(
      "href",
      "/admin/materials/grades/grade-8/subjects/subj-sci",
    );
  });

  it("confirms with a tick when every subject has content", async () => {
    vi.mocked(fetchLearningDirectory).mockResolvedValue({
      subjects: directory.subjects.filter((subject) => subject.id !== "subj-sci"),
    });
    renderPage();
    expect(
      await screen.findByText("Every subject has published topics: 1 subject, 1 topic in total."),
    ).toBeInTheDocument();
  });

  it("counts quizzes on subtopics, not only topic-wide ones", async () => {
    renderPage();
    expect(
      await screen.findByRole("link", { name: "Grade 8 Mathematics: 1 topic, 1 quiz" }),
    ).toBeInTheDocument();
  });

  it("says every subject is doing well when none is below 70%", async () => {
    vi.mocked(fetchDashboardSummary).mockResolvedValue({
      ...summary,
      subjects: summary.subjects.map((subject) =>
        subject.average_mastery === null ? subject : { ...subject, average_mastery: 70.1 },
      ),
    });
    renderPage();
    expect(
      await screen.findByText("Every subject with enough quiz results is averaging 70% or above."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Grade 8 · Science")).not.toBeInTheDocument();
  });

  it("keeps the other sections working when one request fails", async () => {
    vi.mocked(fetchDashboardSummary).mockRejectedValue(new Error("Server unavailable"));
    renderPage();
    expect(await screen.findByText("Server unavailable")).toBeInTheDocument();
    expect(await screen.findByText("None yet")).toBeInTheDocument();
    expect(screen.getByText("2 students")).toBeInTheDocument();
  });

  it("says so plainly when nobody is flagged", async () => {
    vi.mocked(fetchAtRiskFlags).mockResolvedValue({ rows_returned: 0, items: [] });
    renderPage();
    expect(
      await screen.findByText("No students are flagged as at risk right now."),
    ).toBeInTheDocument();
  });
});
