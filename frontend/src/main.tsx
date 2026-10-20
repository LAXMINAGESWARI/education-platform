import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { MotionConfig } from "motion/react";
import { BrowserRouter } from "react-router-dom";

import { AmbientBackdrop } from "./components/AmbientBackdrop";
import { App } from "./App";
import { AuthProvider } from "./auth/AuthContext";
import { PwaPromptRegistry, PwaPrompts } from "./components/PwaPrompts";
import { installHapticFeedback } from "./lib/haptics";
import { ensureInstallPromptListener } from "./pwa/installPrompt";
import "./styles/tokens.css";
import "./styles/app.css";
import "katex/dist/katex.min.css";

installHapticFeedback();
ensureInstallPromptListener();

const root = document.getElementById("root");
if (!root) {
  throw new Error("Root element #root not found");
}

createRoot(root).render(
  <StrictMode>
    <MotionConfig reducedMotion="user">
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AmbientBackdrop />
        <AuthProvider>
          <PwaPromptRegistry>
            <PwaPrompts />
            <App />
          </PwaPromptRegistry>
        </AuthProvider>
      </BrowserRouter>
    </MotionConfig>
  </StrictMode>,
);
