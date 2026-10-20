import { act, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProgressReconnectStatus, RECONNECT_STATUS_DELAY_MS } from "./ProgressReconnect";

describe("ProgressReconnectStatus", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("keeps one live region mounted and waits before announcing a reconnect", async () => {
    vi.useFakeTimers();
    const view = render(<ProgressReconnectStatus active={false} />);
    const slot = screen.getByTestId("progress-reconnect");
    const live = within(slot).getByRole("status");
    expect(live).toHaveAttribute("aria-live", "polite");
    expect(live).not.toHaveTextContent("Reconnecting");

    view.rerender(<ProgressReconnectStatus active />);
    expect(live).toBeInTheDocument();
    expect(screen.queryByText("Reconnecting…")).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(RECONNECT_STATUS_DELAY_MS - 1);
    });
    expect(screen.queryByText("Reconnecting…")).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(within(slot).getByRole("status")).toHaveTextContent("Reconnecting…");
    expect(within(slot).getByRole("status")).toBe(live);

    view.rerender(<ProgressReconnectStatus active={false} />);
    expect(within(slot).getByRole("status")).toBe(live);
    expect(live).not.toHaveTextContent("Reconnecting");
  });

  it("stays quiet when the reconnect finishes inside the delay", async () => {
    vi.useFakeTimers();
    const view = render(<ProgressReconnectStatus active />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RECONNECT_STATUS_DELAY_MS - 1);
    });
    view.rerender(<ProgressReconnectStatus active={false} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(RECONNECT_STATUS_DELAY_MS);
    });
    expect(screen.queryByText("Reconnecting…")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});
