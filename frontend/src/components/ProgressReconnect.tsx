import { useEffect, useState } from "react";

import { prefersReducedMotion } from "../lib/motionPrefs";
import { PushButton } from "./PushButton";

type ProgressReconnectStatusProps = {
  active: boolean;
};

/** A one-heartbeat reconnect finishes inside this window, so the row stays quiet. */
export const RECONNECT_STATUS_DELAY_MS = 2_000;

/** Reserved row so the reconnecting pill does not push the stage cards. */
export function ProgressReconnectStatus({ active }: ProgressReconnectStatusProps) {
  const reducedMotion = prefersReducedMotion();
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!active) {
      setVisible(false);
      return;
    }
    const timer = window.setTimeout(() => setVisible(true), RECONNECT_STATUS_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [active]);

  const show = active && visible;
  return (
    <div
      className="progress-reconnect"
      data-testid="progress-reconnect"
      data-active={active ? "true" : "false"}
      data-reserve="fixed"
    >
      <p
        className={show ? "badge badge--warn progress-reconnect__pill" : "progress-reconnect__live"}
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {show ? (
          <span
            className={reducedMotion ? "progress-reconnect__mark" : "progress-reconnect__spinner"}
            data-motion={reducedMotion ? "reduced" : "spin"}
            aria-hidden="true"
          />
        ) : null}
        {show ? "Reconnecting…" : ""}
      </p>
    </div>
  );
}

type ProgressStreamAlertProps = {
  message: string;
  busy?: boolean;
  onRetry: () => void;
};

export function ProgressStreamAlert({ message, busy = false, onRetry }: ProgressStreamAlertProps) {
  return (
    <div className="progress-stream-error" role="alert">
      <p className="progress-stream-error__message">{message}</p>
      <PushButton type="button" size="sm" onClick={onRetry} disabled={busy} loading={busy}>
        Retry
      </PushButton>
    </div>
  );
}
