import { useState, useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/contexts/auth-context";
import { api, ApiError } from "@/api/client";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

export default function LoginPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { user, login, register } = useAuth();
  const { t } = useTranslation();
  const inviteToken = searchParams.get("invite") || "";

  const [mode, setMode] = useState<"login" | "register">(inviteToken ? "register" : "login");
  const [registrationOpen, setRegistrationOpen] = useState(false);
  // first run = no users yet (next registrant becomes admin); distinct from
  // public signup (admin enabled open self-registration as viewer).
  const [firstRun, setFirstRun] = useState(false);
  const [oauthProviders, setOauthProviders] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  // Keep the login page accessible during demo mode so admins can still sign in.
  useEffect(() => {
    if (user && user.role !== "public_manager") {
      navigate("/", { replace: true });
    }
  }, [user, navigate]);

  // Check if registration is open (first-run)
  useEffect(() => {
    api.authSetupStatus()
      .then((s) => {
        setRegistrationOpen(s.registration_open);
        setFirstRun(s.first_run);
        setOauthProviders(s.oauth_providers ?? []);
        // Force register only on first run; under public signup keep login the
        // default (existing users sign in) while still allowing registration.
        if (s.first_run) setMode("register");
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  // Surface an OAuth failure the backend redirected back with (?oauth_error=…).
  useEffect(() => {
    const code = searchParams.get("oauth_error");
    if (code) setError(t(`login.oauthError.${code}`, { defaultValue: t("login.oauthError.generic") }));
  }, [searchParams, t]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (mode === "register") {
      if (password !== confirmPassword) {
        setError(t("login.passwordsNoMatch"));
        return;
      }
      if (password.length < 6) {
        setError(t("login.passwordTooShort"));
        return;
      }
      try {
        await register(email, fullName, password, inviteToken || undefined);
        navigate("/", { replace: true });
      } catch (err) {
        setError(err instanceof ApiError ? err.message : t("login.registrationFailed"));
      }
    } else {
      try {
        await login(email, password);
        navigate("/", { replace: true });
      } catch (err) {
        setError(err instanceof ApiError ? err.message : t("login.loginFailed"));
      }
    }
  };

  if (loading) {
    return (
      <div className="flex h-dvh items-center justify-center bg-surface-muted">
        <div className="text-fg-muted">{t("common.loading")}</div>
      </div>
    );
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-surface-muted px-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center">
          <h1 className="text-2xl font-bold text-brand">Lambda ERP</h1>
          <p className="mt-1 text-sm text-fg-muted">
            {firstRun
              ? t("login.taglineFirstRun")
              : mode === "register"
                ? t("login.taglineRegister")
                : t("login.taglineSignIn")}
          </p>
          {user?.role === "public_manager" && (
            <p className="mt-2 text-xs text-amber-700">
              {t("login.demoBanner")}
            </p>
          )}
        </div>

        {error && (
          <div className="rounded-lg bg-rose-50 px-4 py-3 text-sm text-rose-700 ring-1 ring-rose-200">
            {error}
          </div>
        )}

        <Card>
          <form onSubmit={handleSubmit} className="space-y-4">
            {mode === "register" && (
              <Input
                label={t("login.fullName")}
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                required
              />
            )}
            <Input
              label={t("login.email")}
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
            <Input
              label={t("login.password")}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
            {mode === "register" && (
              <Input
                label={t("login.confirmPassword")}
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
              />
            )}
            <Button type="submit" className="w-full">
              {mode === "register" ? (firstRun ? t("login.createAdmin") : t("login.register")) : t("login.signIn")}
            </Button>
          </form>

          {oauthProviders.length > 0 && (
            <div className="mt-4 space-y-3">
              <div className="flex items-center gap-3">
                <div className="h-px flex-1 bg-border" />
                <span className="text-xs uppercase tracking-wide text-fg-muted">{t("login.orContinueWith")}</span>
                <div className="h-px flex-1 bg-border" />
              </div>
              <div className="space-y-2">
                {oauthProviders.map((p) => (
                  <button
                    key={p}
                    type="button"
                    onClick={() => { window.location.href = api.oauthLoginUrl(p, { invite: inviteToken || undefined }); }}
                    className="flex w-full items-center justify-center gap-2 rounded-lg border border-border bg-surface px-4 py-2 text-sm font-medium text-fg transition-colors hover:bg-surface-muted"
                  >
                    <ProviderIcon provider={p} />
                    {t("login.continueWithProvider", { provider: providerLabel(p) })}
                  </button>
                ))}
              </div>
            </div>
          )}
        </Card>

        {user?.role === "public_manager" && (
          <Card>
            <div className="space-y-3">
              <div>
                <h2 className="text-sm font-semibold text-fg">{t("login.browsingTitle")}</h2>
                <p className="mt-1 text-sm text-fg-muted">
                  {t("login.browsingBody")}
                </p>
              </div>
              <Button
                type="button"
                className="w-full"
                onClick={() => navigate("/demo", { replace: true })}
              >
                {t("login.enterDemo")}
              </Button>
            </div>
          </Card>
        )}

        {!firstRun && !inviteToken && (
          <p className="text-center text-sm text-fg-muted">
            {mode === "login" ? (
              <>
                {registrationOpen ? t("login.noAccountPrompt") : t("login.haveInvite")}{" "}
                <button onClick={() => setMode("register")} className="font-medium text-brand transition-colors hover:text-brand/80">
                  {t("login.registerLink")}
                </button>
              </>
            ) : (
              <>
                {t("login.alreadyHaveAccount")}{" "}
                <button onClick={() => setMode("login")} className="font-medium text-brand transition-colors hover:text-brand/80">
                  {t("login.signInLink")}
                </button>
              </>
            )}
          </p>
        )}
      </div>
    </div>
  );
}

function providerLabel(provider: string): string {
  return provider.charAt(0).toUpperCase() + provider.slice(1);
}

function ProviderIcon({ provider }: { provider: string }) {
  if (provider === "google") {
    return (
      <svg className="h-4 w-4" viewBox="0 0 48 48" aria-hidden="true">
        <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
        <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
        <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
        <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
      </svg>
    );
  }
  if (provider === "apple") {
    return (
      <svg className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <path d="M16.365 1.43c0 1.14-.42 2.22-1.14 3.03-.78.9-2.06 1.6-3.12 1.5-.13-1.1.42-2.28 1.08-3.03.78-.9 2.13-1.56 3.18-1.5zM20.7 17.1c-.56 1.3-.83 1.88-1.55 3.03-1 1.6-2.42 3.6-4.18 3.62-1.56.02-1.96-1.02-4.07-1-2.12.01-2.56 1.02-4.12 1.01-1.76-.02-3.1-1.82-4.1-3.42C-.3 16.9-.6 11.16 1.7 8.1c1.16-1.54 3-2.51 4.85-2.51 1.87 0 3.05 1.02 4.6 1.02 1.5 0 2.42-1.02 4.6-1.02 1.62 0 3.34.88 4.56 2.4-4.01 2.2-3.36 7.92 0 9.11z" />
      </svg>
    );
  }
  return null;
}
