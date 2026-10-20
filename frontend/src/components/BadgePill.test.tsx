import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { BadgePill, RatingBadge, ScoreBadge, StatusBadge, SubjectBadge } from "./BadgePill";

describe("BadgePill", () => {
  it("renders a neutral pill and a toned pill", () => {
    render(
      <>
        <BadgePill>MATH</BadgePill>
        <BadgePill tone="ok">Passed</BadgePill>
      </>,
    );

    expect(screen.getByText("MATH")).toHaveClass("badge");
    expect(screen.getByText("MATH")).not.toHaveClass("badge--ok");
    expect(screen.getByText("Passed")).toHaveClass("badge", "badge--ok");
  });

  it("paints subject, status, and score pills", () => {
    render(
      <>
        <SubjectBadge name="Mathematics" />
        <StatusBadge status="in_progress" />
        <StatusBadge status="declining" />
        <ScoreBadge value={82} />
        <ScoreBadge value={74} />
        <ScoreBadge value={40} />
        <ScoreBadge value={null} />
        <RatingBadge value={90} />
      </>,
    );

    expect(screen.getByText("Mathematics")).toHaveClass("badge", "badge--info");
    expect(screen.getByText("In progress")).toHaveClass("badge", "badge--warn");
    expect(screen.getByText("Declining")).toHaveClass("badge", "badge--danger");
    const strong = screen.getByText("82%", { selector: ".badge", exact: false });
    expect(strong).toHaveClass("badge", "badge--ok");
    expect(strong).toHaveTextContent("Strong");
    const onTrack = screen.getByText("74%", { selector: ".badge", exact: false });
    expect(onTrack).toHaveClass("badge", "badge--info");
    expect(onTrack).toHaveTextContent("On track");
    const needsWork = screen.getByText("40%", { selector: ".badge", exact: false });
    expect(needsWork).toHaveClass("badge", "badge--warn");
    expect(needsWork).toHaveTextContent("Needs work");
    expect(screen.getByText("Not attempted")).toHaveClass("badge", "badge--info");
    expect(screen.getByText("Strong")).toHaveClass("badge", "badge--ok");
  });
});
