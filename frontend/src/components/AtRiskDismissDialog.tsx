import { useState } from "react";

import type { AtRiskFlag } from "../api/atRisk";

type AtRiskDismissDialogProps = {
  flag: AtRiskFlag | null;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (note: string) => void;
};

/**
 * AR-4: dismissal needs no justification -- the note is always optional, never required --
 * but the action itself is audited regardless of whether one is given.
 *
 * Not `ConfirmDialog`: that component's body is a plain string with no room for a form
 * field, and nothing else in this codebase pairs a confirm dialog with a text input. Built
 * on the same `.overlay`/`.dialog`/`.dialog__actions` classes so it looks identical to
 * every other dialog, just with a `.field` note added inside.
 */
export function AtRiskDismissDialog({ flag, busy, onCancel, onConfirm }: AtRiskDismissDialogProps) {
  const [note, setNote] = useState("");

  if (!flag) return null;

  return (
    <div className="overlay is-open" role="presentation" onClick={() => !busy && onCancel()}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="dismiss-dialog-title"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="dismiss-dialog-title">Dismiss this flag?</h2>
        <p>
          {flag.student_name}
          {flag.subject ? ` · ${flag.subject}` : " · Attendance"} won't show as at-risk any
          more until it's recomputed and the condition still holds. This is logged either way.
        </p>
        <div className="field">
          <label className="field__label" htmlFor="dismiss-note">
            Note (optional)
          </label>
          <textarea
            id="dismiss-note"
            className="field__input"
            rows={3}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="e.g. Spoke with the student; retaking the topic."
            disabled={busy}
          />
        </div>
        <div className="dialog__actions">
          <button type="button" className="btn btn--sm btn--outline" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn--sm"
            onClick={() => onConfirm(note)}
            disabled={busy}
          >
            {busy ? "Dismissing…" : "Dismiss"}
          </button>
        </div>
      </div>
    </div>
  );
}
