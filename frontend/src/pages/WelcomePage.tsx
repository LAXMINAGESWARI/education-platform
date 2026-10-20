import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { ApiError } from "../api/types";
import {
  DEMO_ACCOUNTS,
  DEMO_PASSWORD,
  DEMO_STUDENT,
  type DemoAccount,
} from "../auth/demoAccounts";
import { useAuth } from "../auth/AuthContext";
import { roleHome } from "../auth/roles";
import { PwaPromptHost } from "../components/PwaPrompts";
import { PushButton } from "../components/PushButton";

const LAST_EMAIL_KEY = "ep_last_email";
const IS_DEV = import.meta.env.DEV;

function initialEmail(): string {
  const last = localStorage.getItem(LAST_EMAIL_KEY)?.trim();
  if (last) return last;
  return IS_DEV ? DEMO_STUDENT.email : "";
}

function initialPassword(email: string): string {
  if (!IS_DEV) return "";
  const known = DEMO_ACCOUNTS.some((account) => account.email === email);
  return known ? DEMO_PASSWORD : "";
}

export function WelcomePage() {
  const { user, loading, signIn } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState(initialEmail);
  const [password, setPassword] = useState(() => initialPassword(initialEmail()));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!loading && user) {
    return <Navigate to={roleHome(user.roles)} replace />;
  }

  function fillAccount(account: DemoAccount) {
    setEmail(account.email);
    setPassword(DEMO_PASSWORD);
    setError(null);
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

  async function onDevSignIn(account: DemoAccount) {
    setError(null);
    setBusy(true);
    try {
      setEmail(account.email);
      setPassword(DEMO_PASSWORD);
      await signIn(account.email, DEMO_PASSWORD);
      localStorage.setItem(LAST_EMAIL_KEY, account.email);
      navigate(account.home, { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : `Could not sign in as ${account.role.toLowerCase()}. Ensure the API is running and ${account.email} is seeded.`,
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app-frame app-frame--login">
      <div className="login">
        <PwaPromptHost />
        <div className="route-motion__page login__motion">
          <div className="login__card">
            <div className="login__brand">CurioEd</div>
            <p className="login__lede">Sign in to explore subjects, topics, and quizzes.</p>
            <div className="login-demos">
              <p className="login-demos__label">
                Demo accounts · password <code>{DEMO_PASSWORD}</code>
              </p>
              <ul>
                {DEMO_ACCOUNTS.map((account) => (
                  <li key={account.email}>
                    <button type="button" className="login-demo" onClick={() => fillAccount(account)}>
                      <span className="login-demo__role">{account.role}</span>
                      <code>{account.email}</code>
                      {account.detail ? (
                        <span className="login-demo__detail">{account.detail}</span>
                      ) : null}
                    </button>
                  </li>
                ))}
              </ul>
            </div>

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
              </div>
            </form>

            {IS_DEV && (
              <div className="login__actions">
                <p className="login-demos__label">DEV sign-in</p>
                {DEMO_ACCOUNTS.map((account) => (
                  <PushButton
                    key={account.email}
                    type="button"
                    variant="soft"
                    disabled={busy}
                    loading={busy}
                    onClick={() => void onDevSignIn(account)}
                  >
                    Enter as {account.role.toLowerCase()}
                  </PushButton>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
