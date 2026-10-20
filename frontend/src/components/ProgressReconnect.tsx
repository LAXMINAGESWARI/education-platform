import { prefersReducedMotion } from "../lib/motionPrefs";
import { PushButton } from "./PushButton";

type ProgressReconnectStatusProps = {
  active: boolean;
};

/** Reserved row so the reconnecting pill does not push the stage cards. */
export function ProgressReconnectStatus({ active }: ProgressReconnectStatusProps) {
  const reducedMotion = prefersReducedMotion();
  return (
    <div
      className="progress-reconnect"
      data-testid="progress-reconnect"
      data-active={active ? "true" : "false"}
      data-reserve="fixed"
    >
      {active ? (
        <p
          className="badge badge--warn progress-reconnect__pill"
          role="status"
          aria-live="polite"
          aria-atomic="true"
        >
          <span
            className={reducedMotion ? "progress-reconnect__mark" : "progress-reconnect__spinner"}
            data-motion={reducedMotion ? "reduced" : "spin"}
            aria-hidden="true"
          />
          Reconnecting…
        </p>
      ) : (
        <span className="progress-reconnect__placeholder" aria-hidden="true" />
      )}
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
