import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useLocation } from "react-router-dom";
import { useRegisterSW } from "virtual:pwa-register/react";

import {
  ensureInstallPromptListener,
  getInstallPrompt,
  promptInstall,
  subscribeInstallPrompt,
  wasInstalled,
} from "../pwa/installPrompt";
import { isIosSafari, isStandaloneDisplay, shouldShowInstallBanner, snoozeInstallBanner } from "../pwa/installState";
import { reloadApp, updateReloadBlocked } from "../pwa/serviceWorkerUpdate";

const UPDATE_CHECK_MS = 60 * 60 * 1000;
const PWA_PROMPT_HOST_ID = "pwa-prompt-host";

const PwaPromptRegisterContext = createContext<(node: HTMLElement | null) => void>(() => undefined);
const PwaPromptHostContext = createContext<HTMLElement | null>(null);

/** Wraps the prompts and the page shells so a host can register after auth, with no route change. */
export function PwaPromptRegistry({ children }: { children: ReactNode }) {
  const [host, setHost] = useState<HTMLElement | null>(null);
  const register = useCallback((node: HTMLElement | null) => {
    setHost(node);
  }, []);
  return (
    <PwaPromptRegisterContext.Provider value={register}>
      <PwaPromptHostContext.Provider value={host}>{children}</PwaPromptHostContext.Provider>
    </PwaPromptRegisterContext.Provider>
  );
}

/** Mount point inside the scrolling page column. The banner flows above the title. */
export function PwaPromptHost() {
  const register = useContext(PwaPromptRegisterContext);
  const ref = useCallback(
    (node: HTMLDivElement | null) => {
      register(node);
    },
    [register],
  );
  return <div id={PWA_PROMPT_HOST_ID} ref={ref} />;
}

export function PwaPrompts() {
  const host = useContext(PwaPromptHostContext);

  const prompts = (
    <div className={host ? "pwa-toasts pwa-toasts--inline" : "pwa-toasts"}>
      <PwaUpdatePrompt />
      <InstallPrompt />
    </div>
  );
  if (host) return createPortal(prompts, host);
  return prompts;
}

function PwaUpdatePrompt() {
  const location = useLocation();
  const onQuiz = updateReloadBlocked(location.pathname);
  const [later, setLater] = useState(false);
  const [updateReady, setUpdateReady] = useState(false);
  const stopRef = useRef<(() => void) | null>(null);
  const {
    needRefresh: [needRefresh],
    updateServiceWorker,
  } = useRegisterSW({
    onNeedReload() {
      setUpdateReady(true);
    },
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

  useEffect(() => {
    if (!updateReady || later || onQuiz) return;
    reloadApp();
  }, [updateReady, later, onQuiz]);

  const showPrompt = (needRefresh || (updateReady && onQuiz)) && !later;
  if (!showPrompt) return null;

  return (
    <div className="pwa-toast" role="status">
      <p>A new version of CurioEd is available.</p>
      <button type="button" className="btn btn--soft btn--sm" onClick={() => setLater(true)}>
        Later
      </button>
      <button
        type="button"
        className="btn btn--sm"
        onClick={() => {
          if (needRefresh) void updateServiceWorker(true);
          else reloadApp();
        }}
      >
        Reload
      </button>
    </div>
  );
}

function InstallPrompt() {
  const [installReady, setInstallReady] = useState(false);
  const [installed, setInstalled] = useState(false);
  const [bannerRevision, setBannerRevision] = useState(0);

  useEffect(() => {
    ensureInstallPromptListener();
    const sync = () => {
      setInstalled(wasInstalled() || isStandaloneDisplay());
      setInstallReady(getInstallPrompt() !== null);
    };
    sync();
    return subscribeInstallPrompt(sync);
  }, []);

  if (installed || isStandaloneDisplay() || !shouldShowInstallBanner()) return null;

  function snooze() {
    snoozeInstallBanner();
    setBannerRevision((value) => value + 1);
  }

  if (installReady) {
    return (
      <div key={bannerRevision} className="pwa-toast" role="region" aria-label="Install CurioEd">
        <p>Install CurioEd on this phone.</p>
        <button type="button" className="btn btn--soft btn--sm" onClick={snooze}>
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

  if (!isIosSafari()) return null;

  return (
    <div key={bannerRevision} className="pwa-toast" role="region" aria-label="Install CurioEd">
      <p>To install CurioEd, tap Share, then Add to Home Screen.</p>
      <button type="button" className="btn btn--soft btn--sm" onClick={snooze}>
        Not now
      </button>
    </div>
  );
}
