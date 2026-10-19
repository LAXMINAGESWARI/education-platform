import { useEffect, useRef, useState } from "react";
import { useRegisterSW } from "virtual:pwa-register/react";

import {
  dismissInstallPrompt,
  ensureInstallPromptListener,
  getInstallPrompt,
  promptInstall,
  subscribeInstallPrompt,
  wasInstalled,
} from "../pwa/installPrompt";
import {
  IOS_HINT_DISMISS_KEY,
  isIosSafari,
  isStandaloneDisplay,
  readDismissed,
  rememberDismissed,
} from "../pwa/installState";

const UPDATE_CHECK_MS = 60 * 60 * 1000;

export function PwaPrompts() {
  return (
    <div className="pwa-toasts">
      <PwaUpdatePrompt />
      <InstallPrompt />
    </div>
  );
}

function PwaUpdatePrompt() {
  const [later, setLater] = useState(false);
  const stopRef = useRef<(() => void) | null>(null);
  const {
    needRefresh: [needRefresh],
    updateServiceWorker,
  } = useRegisterSW({
    onRegisteredSW(_url, registration) {
      if (!registration) return;
      stopRef.current?.();
      const check = () => {
        void registration.update().catch(() => undefined);
      };
      const onVisible = () => {
        if (document.visibilityState === "visible") check();
      };
      const interval = window.setInterval(check, UPDATE_CHECK_MS);
      document.addEventListener("visibilitychange", onVisible);
      stopRef.current = () => {
        window.clearInterval(interval);
        document.removeEventListener("visibilitychange", onVisible);
      };
    },
  });

  useEffect(() => () => stopRef.current?.(), []);

  if (!needRefresh || later) return null;

  return (
    <div className="pwa-toast" role="status">
      <p>A new version of CurioEd is available.</p>
      <button type="button" className="btn btn--soft btn--sm" onClick={() => setLater(true)}>
        Later
      </button>
      <button type="button" className="btn btn--sm" onClick={() => void updateServiceWorker(true)}>
        Reload
      </button>
    </div>
  );
}

function InstallPrompt() {
  const [installReady, setInstallReady] = useState(false);
  const [showIosHint, setShowIosHint] = useState(false);
  const [installed, setInstalled] = useState(false);

  useEffect(() => {
    ensureInstallPromptListener();
    const sync = () => {
      setInstalled(wasInstalled() || isStandaloneDisplay());
      setInstallReady(getInstallPrompt() !== null);
      setShowIosHint(
        !wasInstalled() &&
          !isStandaloneDisplay() &&
          getInstallPrompt() === null &&
          isIosSafari() &&
          !readDismissed(IOS_HINT_DISMISS_KEY),
      );
    };
    sync();
    return subscribeInstallPrompt(sync);
  }, []);

  if (installed || isStandaloneDisplay()) return null;

  if (installReady) {
    return (
      <div className="pwa-toast" role="region" aria-label="Install CurioEd">
        <p>Install CurioEd on this phone.</p>
        <button
          type="button"
          className="btn btn--soft btn--sm"
          onClick={() => {
            dismissInstallPrompt();
          }}
        >
          Not now
        </button>
        <button
          type="button"
          className="btn btn--sm"
          onClick={() => {
            void promptInstall();
          }}
        >
          Install app
        </button>
      </div>
    );
  }

  if (!showIosHint) return null;

  return (
    <div className="pwa-toast" role="region" aria-label="Install CurioEd">
      <p>To install CurioEd, tap Share, then Add to Home Screen.</p>
      <button
        type="button"
        className="btn btn--soft btn--sm"
        onClick={() => {
          rememberDismissed(IOS_HINT_DISMISS_KEY);
          setShowIosHint(false);
        }}
      >
        Not now
      </button>
    </div>
  );
}
