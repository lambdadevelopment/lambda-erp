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
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <div className="text-center">
        {error ? (
          <>
            <p className="text-sm text-red-600">{error}</p>
            <button
              onClick={() => navigate("/login", { replace: true })}
              className="mt-4 text-sm text-blue-600 hover:text-blue-800"
            >
              Back to login
            </button>
          </>
        ) : (
          <p className="text-sm text-gray-400">Starting live demo...</p>
        )}
      </div>
    </div>
  );
}
