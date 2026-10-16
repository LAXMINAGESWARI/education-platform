import { useEffect, useRef, useState } from "react";
import { Outlet, useNavigate } from "react-router-dom";

import { resetDemoProgress } from "../api/demo";
import { ApiError } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { ConfirmDialog } from "./ConfirmDialog";
import { RouteMotion } from "./RouteMotion";

const IS_DEV = import.meta.env.DEV;

function initialsFromName(name: string | undefined): string {
  const parts = (name ?? "A").trim().split(/\s+/).filter(Boolean);
  const letters = (parts[0]?.[0] ?? "A") + (parts[1]?.[0] ?? "");
  return letters.toUpperCase();
}

export function AppShell({ children }: { children?: React.ReactNode }) {
  const page = children ?? <Outlet />;
  const { user, enrolled, signOut } = useAuth();
  const navigate = useNavigate();
  const menuRef = useRef<HTMLDivElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);

  useEffect(() => {
    if (!menuOpen) return;

    const onPointerDown = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [menuOpen]);

  async function onResetDemo() {
    setResetBusy(true);
    setResetError(null);
    try {
      await resetDemoProgress();
      setResetOpen(false);
      navigate("/", { replace: true });
      window.location.assign("/");
    } catch (err) {
      setResetError(err instanceof ApiError ? err.message : "Could not reset demo.");
      setResetBusy(false);
    }
  }

  return (
    <div className="app-frame">
      <div ref={menuRef} className="profile-dock">
        <button
          type="button"
          className="profile-dock__avatar"
          aria-label="Account menu"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          title={user?.full_name ?? "Account"}
          onClick={() => setMenuOpen((open) => !open)}
        >
          {initialsFromName(user?.full_name)}
        </button>
        {menuOpen && (
          <div className="profile-dock__menu" role="menu" aria-label="Account">
            <p className="profile-dock__name">{user?.full_name ?? "Student"}</p>
            {IS_DEV && enrolled && (
              <button
                type="button"
                className="profile-dock__item"
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  setResetError(null);
                  setResetOpen(true);
                }}
              >
                Reset demo
              </button>
            )}
            <button
              type="button"
              className="profile-dock__item"
              role="menuitem"
              onClick={() => void signOut()}
            >
              Sign out
            </button>
          </div>
        )}
      </div>

      <div className="app app--flush">
        <div className="app__content">
          <main className="main">
            <RouteMotion>{page}</RouteMotion>
          </main>
        </div>
      </div>

      <ConfirmDialog
        open={resetOpen}
        title="Demo reset"
        body={
          resetError ??
          "All quiz progress and attempt history will be cleared. Subjects start fresh."
        }
        onDismiss={() => {
          if (!resetBusy) setResetOpen(false);
        }}
        actions={[
          { label: "Cancel", variant: "soft" },
          {
            label: resetBusy ? "Resetting…" : "Reset demo",
            keepOpen: true,
            onClick: () => {
              void onResetDemo();
            },
          },
        ]}
      />
    </div>
  );
}
