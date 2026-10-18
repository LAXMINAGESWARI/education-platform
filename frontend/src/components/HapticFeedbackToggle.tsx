import { useState } from "react";

import { hapticsSupported, isHapticsEnabled, setHapticsEnabled } from "../lib/haptics";

/** Device setting. Renders nothing where haptics cannot fire (desktop). */
export function HapticFeedbackToggle() {
  const [enabled, setEnabled] = useState(isHapticsEnabled);
  if (!hapticsSupported()) return null;

  return (
    <label className="haptic-toggle">
      <input
        type="checkbox"
        role="switch"
        checked={enabled}
        onChange={(event) => {
          setHapticsEnabled(event.target.checked);
          setEnabled(event.target.checked);
        }}
      />
      Haptic feedback
    </label>
  );
}
