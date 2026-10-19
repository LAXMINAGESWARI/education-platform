import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";

import {
  ensureInstallPromptListener,
  promptInstall,
  subscribeInstallPrompt,
  wasInstalled,
} from "../pwa/installPrompt";
import {
  type InstallPlatform,
  installPlatform,
  isIosSafari,
  isStandaloneDisplay,
} from "../pwa/installState";

const INSTRUCTIONS: Record<InstallPlatform, string> = {
  android: "Android Chrome: open the menu (⋮), then tap Install app or Add to Home screen.",
  ios: "iPhone Safari: tap Share, then Add to Home Screen.",
  desktop: "Desktop Chrome or Edge: use the install icon in the address bar.",
};

export function InstallAppButton() {
  const [installed, setInstalled] = useState(() => wasInstalled() || isStandaloneDisplay());
  const [sheetOpen, setSheetOpen] = useState(false);

  useEffect(() => {
    ensureInstallPromptListener();
    const sync = () => setInstalled(wasInstalled() || isStandaloneDisplay());
    sync();
    const unsubscribe = subscribeInstallPrompt(sync);
    const queries =
      typeof window.matchMedia === "function"
        ? ["(display-mode: standalone)", "(display-mode: fullscreen)"].map((query) => window.matchMedia(query))
        : [];
    for (const query of queries) query.addEventListener("change", sync);
    return () => {
      unsubscribe();
      for (const query of queries) query.removeEventListener("change", sync);
    };
  }, []);

  if (installed) return null;

  return (
    <>
      <button
        type="button"
        className="rail__link nav-drawer__install"
        onClick={() =>
          promptInstall().then((outcome) => {
            if (outcome === "unavailable") setSheetOpen(true);
          })
        }
      >
        Install app
      </button>
      {sheetOpen ? <InstallInstructionsSheet onClose={() => setSheetOpen(false)} /> : null}
    </>
  );
}

function InstallInstructionsSheet({ onClose }: { onClose: () => void }) {
  const titleId = useId();
  const platform = installPlatform();
  const steps = instructionOrder(platform);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return createPortal(
    <div className="pwa-sheet-backdrop" onClick={onClose}>
      <div
        className="pwa-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id={titleId}>Install CurioEd</h2>
        {platform === "ios" && !isIosSafari() ? (
          <p>Open this page in Safari, then follow the iPhone steps.</p>
        ) : (
          <p>If the browser did not show an install prompt, use the steps for this device.</p>
        )}
        <ul className="pwa-sheet__steps">
          {steps.map((step) => (
            <li key={step}>{INSTRUCTIONS[step]}</li>
          ))}
        </ul>
        <button type="button" className="btn btn--soft" onClick={onClose}>
          Close
        </button>
      </div>
    </div>,
    document.body,
  );
}

function instructionOrder(platform: InstallPlatform): InstallPlatform[] {
  const rest = (["android", "ios", "desktop"] as const).filter((item) => item !== platform);
  switch (platform) {
    case "android":
    case "ios":
    case "desktop":
      return [platform, ...rest];
    default: {
      const neverPlatform: never = platform;
      return neverPlatform;
    }
  }
}
