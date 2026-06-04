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
        // Force register only on first run; under public signup keep login the
        // default (existing users sign in) while still allowing registration.
        if (s.first_run) setMode("register");
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

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
