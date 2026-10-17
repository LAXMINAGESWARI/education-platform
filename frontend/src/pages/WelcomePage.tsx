import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { bootstrapDemoProgress } from "../api/demo";
import { enrollPocMath } from "../api/enrollments";
import { ApiError } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { roleHome } from "../auth/roles";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { PushButton } from "../components/PushButton";

const DEMO_EMAIL = "student@demo.school";
const DEMO_PASSWORD = "demo1234";
const DEMO_ADMIN_EMAIL = "admin@demo.school";
const DEMO_ADMIN_PASSWORD = "demo1234";
const DEMO_TEACHER_EMAIL = "meera.krishnan@alnoor.school";
const DEMO_TEACHER_PASSWORD = "demo1234";
const LAST_EMAIL_KEY = "ep_last_email";
const IS_DEV = import.meta.env.DEV;

function initialEmail(): string {
  const last = localStorage.getItem(LAST_EMAIL_KEY)?.trim();
  if (last) return last;
  return IS_DEV ? DEMO_EMAIL : "";
}

function initialPassword(email: string): string {
  return IS_DEV && email === DEMO_EMAIL ? DEMO_PASSWORD : "";
}

export function WelcomePage() {
  const { user, loading, signIn, setEnrollments } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState(initialEmail);
  const [password, setPassword] = useState(() => initialPassword(initialEmail()));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [holdingForDemo, setHoldingForDemo] = useState(false);
  const [quickDialog, setQuickDialog] = useState<{
    title: string;
    body: string;
    subjectPath: string;
  } | null>(null);

  if (!loading && user && !holdingForDemo && !quickDialog) {
    return <Navigate to={roleHome(user.roles)} replace />;
  }

  async function doSignIn(nextEmail: string, nextPassword: string) {
    setError(null);
    setBusy(true);
    try {
      const trimmed = nextEmail.trim();
      await signIn(trimmed, nextPassword);
      localStorage.setItem(LAST_EMAIL_KEY, trimmed);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    await doSignIn(email, password);
  }

  async function onQuickDemo() {
    setError(null);
    setBusy(true);
    setHoldingForDemo(true);
    try {
      setEmail(DEMO_EMAIL);
      setPassword(DEMO_PASSWORD);
      await signIn(DEMO_EMAIL, DEMO_PASSWORD);
      const summary = await enrollPocMath(true);
      setEnrollments(summary);
      const boot = await bootstrapDemoProgress();
      const subjectPath = `/subjects/${boot.subject_id}`;
      setQuickDialog({
        title: "Quick demo ready",
        body: boot.message,
        subjectPath,
      });
    } catch (err) {
      setHoldingForDemo(false);
      setError(err instanceof ApiError ? err.message : "Could not start quick demo.");
    } finally {
      setBusy(false);
    }
  }

  function finishQuickDemo() {
    const path = quickDialog?.subjectPath ?? "/";
    setQuickDialog(null);
    setHoldingForDemo(false);
    navigate(path, { replace: true });
  }

  async function onDevAdmin() {
    setError(null);
    setBusy(true);
    try {
      await signIn(DEMO_ADMIN_EMAIL, DEMO_ADMIN_PASSWORD);
      navigate("/admin", { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not sign in as admin. Ensure the API is running and admin@demo.school is seeded.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function onDevTeacher() {
    setError(null);
    setBusy(true);
    try {
      await signIn(DEMO_TEACHER_EMAIL, DEMO_TEACHER_PASSWORD);
      navigate("/teacher", { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not sign in as teacher. Ensure the API is running and the synthetic school is seeded.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app-frame">
      <div className="demo-banner" role="status">
        Demo mockup only · Calm Humanist · Source Sans 3 + IBM Plex Mono · live API
      </div>
      <div className="login">
        <div className="login__card">
          <div className="login__brand">CurioEd</div>
          <p className="login__lede">Sign in to explore subjects, topics, and quizzes.</p>
          <p className="hint">
            Demo login: <code>{DEMO_EMAIL}</code> / <code>{DEMO_PASSWORD}</code>
          </p>

          <form className="form" onSubmit={(e) => void onSubmit(e)} noValidate>
            <div className="form__field">
              <label className="form__label" htmlFor="email">
                Email
              </label>
              <input
                id="email"
                type="email"
                className="form__input"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="username"
              />
            </div>
            <div className="form__field">
              <label className="form__label" htmlFor="password">
                Password
              </label>
              <input
                id="password"
                type="password"
                className="form__input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={8}
                autoComplete="current-password"
              />
            </div>
            {error && (
              <p className="form__error" role="alert">
                {error}
              </p>
            )}
            <div className="login__actions">
              <PushButton type="submit" size="lg" loading={busy}>
                Sign in
              </PushButton>
              {IS_DEV && (
                <PushButton
                  type="button"
                  variant="soft"
                  disabled={busy}
                  onClick={() => void onQuickDemo()}
                >
                  Quick demo (unlock topic quiz)
                </PushButton>
              )}
            </div>
          </form>

          {IS_DEV && (
            <div className="login__actions" style={{ marginTop: "1rem" }}>
              <p className="hint" style={{ width: "100%", marginBottom: "0.5rem" }}>
                DEV shortcuts: admin (
                <code>{DEMO_ADMIN_EMAIL}</code>) and teacher (
                <code>{DEMO_TEACHER_EMAIL}</code>) both use a real JWT. Password{" "}
                <code>{DEMO_TEACHER_PASSWORD}</code>.
              </p>
              <PushButton
                type="button"
                variant="soft"
                disabled={busy}
                loading={busy}
                onClick={() => void onDevAdmin()}
              >
                Enter as admin
              </PushButton>
              <PushButton
                type="button"
                variant="soft"
                disabled={busy}
                loading={busy}
                onClick={() => void onDevTeacher()}
              >
                Enter as teacher
              </PushButton>
            </div>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={quickDialog != null}
        title={quickDialog?.title ?? ""}
        body={quickDialog?.body ?? ""}
        onDismiss={finishQuickDemo}
        actions={[{ label: "Got it" }]}
      />
    </div>
  );
}
