import { useState, useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "@/contexts/auth-context";
import { api, ApiError } from "@/api/client";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

export default function LoginPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { user, login, register } = useAuth();
  const inviteToken = searchParams.get("invite") || "";

  const [mode, setMode] = useState<"login" | "register">(inviteToken ? "register" : "login");
  const [registrationOpen, setRegistrationOpen] = useState(false);
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
        if (s.registration_open) setMode("register");
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (mode === "register") {
      if (password !== confirmPassword) {
        setError("Passwords do not match");
        return;
      }
      if (password.length < 6) {
        setError("Password must be at least 6 characters");
        return;
      }
      try {
        await register(email, fullName, password, inviteToken || undefined);
        navigate("/", { replace: true });
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Registration failed");
      }
    } else {
      try {
        await login(email, password);
        navigate("/", { replace: true });
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Login failed");
      }
    }
  };

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center bg-gray-50">
        <div className="text-gray-400">Loading...</div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center">
          <h1 className="text-2xl font-bold text-blue-600">Lambda ERP</h1>
          <p className="mt-1 text-sm text-gray-500">
            {registrationOpen
              ? "Create your admin account to get started"
              : mode === "register"
                ? "Create your account"
                : "Sign in to your account"}
          </p>
          {user?.role === "public_manager" && (
            <p className="mt-2 text-xs text-amber-700">
              Demo mode is active. Sign in with an admin account to manage settings or disable public access.
            </p>
          )}
        </div>

        {error && (
          <div className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <Card>
          <form onSubmit={handleSubmit} className="space-y-4">
            {mode === "register" && (
              <Input
                label="Full Name"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                required
              />
            )}
            <Input
              label="Email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
            <Input
              label="Password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
            {mode === "register" && (
              <Input
                label="Confirm Password"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
              />
            )}
            <Button type="submit" className="w-full">
              {mode === "register" ? (registrationOpen ? "Create Admin Account" : "Register") : "Sign In"}
            </Button>
          </form>
        </Card>

        {user?.role === "public_manager" && (
          <Card>
            <div className="space-y-3">
              <div>
                <h2 className="text-sm font-semibold text-gray-900">Just browsing?</h2>
                <p className="mt-1 text-sm text-gray-500">
                  Start a fresh chat session and watch the live demo.
                </p>
              </div>
              <Button
                type="button"
                className="w-full"
                onClick={() => navigate("/demo", { replace: true })}
              >
                Enter Live Demo
              </Button>
            </div>
          </Card>
        )}

        {!registrationOpen && !inviteToken && (
          <p className="text-center text-sm text-gray-500">
            {mode === "login" ? (
              <>
                Have an invite?{" "}
                <button onClick={() => setMode("register")} className="font-medium text-blue-600 hover:text-blue-800">
                  Register
                </button>
              </>
            ) : (
              <>
                Already have an account?{" "}
                <button onClick={() => setMode("login")} className="font-medium text-blue-600 hover:text-blue-800">
                  Sign in
                </button>
              </>
            )}
          </p>
        )}
      </div>
    </div>
  );
}
