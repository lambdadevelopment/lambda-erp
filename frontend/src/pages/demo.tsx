import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "@/api/client";

/**
 * Shareable entry point that mirrors the "Enter Live Demo" button on /login.
 * Creates a fresh chat session (as the public_manager fallback user if the
 * visitor has no cookie) and redirects to /chat/{id}?demo=1 for replay.
 */
export default function DemoPage() {
  const navigate = useNavigate();
  const startedRef = useRef(false);
  const [error, setError] = useState<string>("");

  useEffect(() => {
    // StrictMode double-mounts the effect. A `let cancelled` flag flipped in
    // cleanup would still be `true` when the (single) fetch resolves on the
    // second mount — the promise closure captures the first mount's variable.
    // Guard duplicate kickoffs with a ref and drop the cancelled flag entirely.
    if (startedRef.current) return;
    startedRef.current = true;

    api.createChatSession()
      .then((session) => {
        navigate(`/chat/${session.id}?demo=1`, { replace: true });
      })
      .catch((err) => {
        setError(err instanceof ApiError ? err.message : "Could not start demo");
      });
  }, [navigate]);

  return (
    <div className="flex min-h-dvh items-center justify-center bg-surface-muted px-4">
      <div className="text-center">
        {error ? (
          <>
            <p className="text-sm text-rose-600">{error}</p>
            <button
              onClick={() => navigate("/login", { replace: true })}
              className="mt-4 text-sm text-brand transition-colors hover:text-brand/80"
            >
              Back to login
            </button>
          </>
        ) : (
          <div className="space-y-3">
            <div className="mx-auto h-2 w-2 animate-pulse rounded-full bg-brand" />
            <p className="text-sm text-fg-muted">Starting live demo...</p>
          </div>
        )}
      </div>
    </div>
  );
}
