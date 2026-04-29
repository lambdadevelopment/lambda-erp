import { useState, useEffect, useLayoutEffect, useRef, useCallback } from "react";
import { Link, useParams, useNavigate, useSearchParams, useLocation } from "react-router-dom";
import { type ChatMessage, type ChatAttachment, useChat, rememberSessionId } from "@/components/chat/chat-provider";
import { api } from "@/api/client";

const MAX_ATTACHMENTS = 5;
const MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024;
const ALLOWED_MIMES = new Set([
  "image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf",
]);

interface PendingAttachment {
  localId: string;
  file: File;
  previewUrl?: string;
  uploadedId?: string;
  uploading: boolean;
  error?: string;
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function formatTime(ts?: string): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return "";

  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();

  if (sameDay) {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  const sameYear = d.getFullYear() === now.getFullYear();
  if (sameYear) {
    return d.toLocaleDateString([], { day: "numeric", month: "short" });
  }

  return d.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
}

function formatFullTimestamp(ts?: string): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString([], {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
    timeZoneName: "short",
  });
}

export default function ChatPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const demoRequested = searchParams.get("demo") === "1";
  const [input, setInput] = useState("");
  const [demoError, setDemoError] = useState("");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [attachmentError, setAttachmentError] = useState("");
  const [isDragOver, setIsDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const {
    connectionStatus,
    connectionVersion,
    createSession,
    startDemo,
    completeDemoTyping,
    loadHistory,
    loadMoreHistory,
    clearHistory,
    sendMessage,
    getMessages,
    hasMoreHistory,
    isLoadingOlder,
    isThinking: getIsThinking,
    getDemoStatus,
    getDemoTypingCue,
  } = useChat();
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const previousThinkingRef = useRef(false);
  const demoRunStartedRef = useRef<string | null>(null);
  const demoTypingRunIdRef = useRef(0);
  const shouldAutoScrollRef = useRef(true);
  const pendingPrependHeightRef = useRef<number | null>(null);
  // True while we're driving the scroll ourselves (smooth-scroll animation
  // or ResizeObserver-driven pin-to-bottom). Scroll events fired by those
  // animations must NOT be interpreted as the user scrolling up, or else
  // shouldAutoScrollRef flips to false mid-animation and every subsequent
  // rescroll gets gated out.
  const programmaticScrollRef = useRef(false);
  const programmaticScrollTimerRef = useRef<number | null>(null);
  const messages = getMessages(sessionId);
  const isConnected = connectionStatus === "connected";
  const isThinking = getIsThinking(sessionId);
  const demoStatus = getDemoStatus(sessionId);
  const demoTypingCue = getDemoTypingCue(sessionId);
  const isDemoReplaying = demoRequested || demoStatus === "running";
  const canLoadOlder = hasMoreHistory(sessionId);
  const loadingOlder = isLoadingOlder(sessionId);

  const lastMessage = messages[messages.length - 1];
  const lastMessageKey = lastMessage
    ? `${lastMessage.type}:${lastMessage.timestamp || ""}:${lastMessage.content}`
    : "empty";

  // Flag programmatic scrolls so the onScroll handler doesn't interpret
  // the browser's mid-animation scroll events as "user scrolled up".
  // Refreshes the reset timer each call — a rescroll inside 600ms
  // keeps the flag on for the full animation + reflow window.
  const markProgrammaticScroll = useCallback(() => {
    programmaticScrollRef.current = true;
    if (programmaticScrollTimerRef.current !== null) {
      window.clearTimeout(programmaticScrollTimerRef.current);
    }
    programmaticScrollTimerRef.current = window.setTimeout(() => {
      programmaticScrollRef.current = false;
      programmaticScrollTimerRef.current = null;
    }, 600);
  }, []);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    const el = scrollContainerRef.current;
    if (!el) return;
    // Drive the container directly instead of relying on
    // scrollIntoView — it's more predictable for tall messages and
    // avoids quirks around the zero-height end-of-list marker.
    markProgrammaticScroll();
    el.scrollTo({ top: el.scrollHeight, behavior });
    // A tall, freshly-mounted assistant bubble (markdown tables, long
    // lists) can still reflow after the effect commits — re-check once
    // a frame later, and once more after ~100ms for stragglers like
    // late-rendered images or resize-observed blocks. If the user has
    // already scrolled up manually, leave them alone.
    const rescroll = () => {
      const current = scrollContainerRef.current;
      if (!current) return;
      if (!shouldAutoScrollRef.current && !isDemoReplaying && !isThinking) return;
      if (current.scrollTop + current.clientHeight >= current.scrollHeight - 2) return;
      markProgrammaticScroll();
      current.scrollTo({ top: current.scrollHeight, behavior: "auto" });
    };
    requestAnimationFrame(rescroll);
    window.setTimeout(rescroll, 120);
  }, [isDemoReplaying, isThinking, markProgrammaticScroll]);

  useLayoutEffect(() => {
    const el = scrollContainerRef.current;
    const prevHeight = pendingPrependHeightRef.current;
    if (!el || prevHeight === null) return;
    const delta = el.scrollHeight - prevHeight;
    if (delta > 0) {
      el.scrollTop = el.scrollTop + delta;
    }
    pendingPrependHeightRef.current = null;
  }, [messages.length]);

  useEffect(() => {
    if (pendingPrependHeightRef.current !== null) return;
    if (!shouldAutoScrollRef.current && !isDemoReplaying && !isThinking) return;
    scrollToBottom(isDemoReplaying ? "auto" : "smooth");
  }, [lastMessageKey, isDemoReplaying, isThinking, scrollToBottom]);

  // Chase any post-commit layout growth (markdown tables, late-rendered
  // links, web-font reflows, images finalising their box). ResizeObserver
  // fires whenever the scroll container's content box changes size; if the
  // user hasn't manually scrolled up, keep pinning the view to the bottom.
  useEffect(() => {
    const el = scrollContainerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    let raf = 0;
    const observer = new ResizeObserver(() => {
      if (pendingPrependHeightRef.current !== null) return;
      if (!shouldAutoScrollRef.current && !isDemoReplaying && !isThinking) return;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const current = scrollContainerRef.current;
        if (!current) return;
        const distanceFromBottom =
          current.scrollHeight - current.scrollTop - current.clientHeight;
        // Only chase when we're already near the bottom — avoids yanking the
        // view when the user is scrolled up reading history.
        if (distanceFromBottom <= 120) {
          markProgrammaticScroll();
          current.scrollTo({ top: current.scrollHeight, behavior: "auto" });
        }
      });
    });
    // Observe the content wrapper so tall new bubbles trigger the callback.
    const content = el.firstElementChild;
    if (content) observer.observe(content);
    observer.observe(el);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(raf);
    };
  }, [isDemoReplaying, isThinking, markProgrammaticScroll]);

  const handleLoadOlder = useCallback(() => {
    if (!sessionId) return;
    if (loadingOlder || !canLoadOlder) return;
    const el = scrollContainerRef.current;
    pendingPrependHeightRef.current = el ? el.scrollHeight : null;
    shouldAutoScrollRef.current = false;
    void loadMoreHistory(sessionId).catch(() => {
      pendingPrependHeightRef.current = null;
    });
  }, [sessionId, loadingOlder, canLoadOlder, loadMoreHistory]);

  useEffect(() => {
    setInput("");
    setDemoError("");
    demoRunStartedRef.current = null;
    demoTypingRunIdRef.current += 1;
    // Remember that this browser participated in this chat, so the sidebar
    // keeps showing it even when the server-side filter hides demo-only
    // sessions for the shared public_manager account.
    if (sessionId) rememberSessionId(sessionId);
  }, [sessionId, demoRequested]);

  useEffect(() => {
    const state = location.state as { prefillMessage?: string } | null;
    if (!state?.prefillMessage) return;
    setInput(state.prefillMessage);
    window.setTimeout(() => inputRef.current?.focus(), 50);
    navigate(location.pathname + location.search, { replace: true, state: null });
  }, [location.pathname, location.search, location.state, navigate]);

  // If no sessionId, create one and redirect
  useEffect(() => {
    if (sessionId || !isConnected) return;

    let cancelled = false;
    createSession()
      .then((session) => {
        if (!cancelled) {
          navigate(`/chat/${session.id}`, { replace: true });
        }
      })
      .catch(() => {
        // ignore
      });

    return () => {
      cancelled = true;
    };
  }, [sessionId, isConnected, navigate]);

  const loadHistoryRef = useRef(loadHistory);
  useEffect(() => {
    loadHistoryRef.current = loadHistory;
  }, [loadHistory]);

  useEffect(() => {
    if (!sessionId || !isConnected) return;
    if (demoRequested || demoStatus === "running") return;
    void loadHistoryRef.current(sessionId).catch(() => {
      // ignore
    });
  }, [sessionId, isConnected, connectionVersion, demoRequested, demoStatus]);

  useEffect(() => {
    if (previousThinkingRef.current && !isThinking && isConnected) {
      setTimeout(() => inputRef.current?.focus(), 50);
    }
    previousThinkingRef.current = isThinking;
  }, [isThinking, isConnected]);

  function clearCurrentHistory() {
    if (!sessionId) return;
    void clearHistory(sessionId).catch(() => {
      // ignore
    });
  }

  function handleMessagesScroll(e: React.UIEvent<HTMLDivElement>) {
    // Ignore scroll events fired by our own programmatic scrolls —
    // mid-animation positions are meaningless for "is the user reading
    // history?" and flipping shouldAutoScrollRef mid-animation defeats
    // the whole auto-scroll chain.
    if (programmaticScrollRef.current) return;
    const el = e.currentTarget;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    shouldAutoScrollRef.current = distanceFromBottom < 80;
  }

  useEffect(() => {
    if (!sessionId || !isConnected || !demoRequested) return;
    if (demoRunStartedRef.current === sessionId) return;

    demoRunStartedRef.current = sessionId;
    setDemoError("");
    void startDemo(sessionId).catch((err) => {
      demoRunStartedRef.current = null;
      setDemoError(err instanceof Error ? err.message : "Could not start demo");
    });
  }, [sessionId, isConnected, demoRequested, startDemo]);

  useEffect(() => {
    if (!sessionId || !demoRequested || demoStatus !== "complete") return;
    setInput("");
    navigate(`/chat/${sessionId}`, { replace: true });
  }, [sessionId, demoRequested, demoStatus, navigate]);

  const completeDemoTypingRef = useRef(completeDemoTyping);
  useEffect(() => {
    completeDemoTypingRef.current = completeDemoTyping;
  }, [completeDemoTyping]);

  const typedCueSeqRef = useRef<number | null>(null);
  useEffect(() => {
    if (!demoTypingCue || !sessionId) return;
    // Guard against re-runs for the same cue (provider re-renders recreate object refs)
    if (typedCueSeqRef.current === demoTypingCue.seq) return;
    typedCueSeqRef.current = demoTypingCue.seq;

    const targetSessionId = sessionId;
    const typingContent = demoTypingCue.content;
    const charDelay = typingContent.length > 0
      ? Math.max(8, demoTypingCue.typingMs / typingContent.length)
      : 0;
    const typingSeq = demoTypingCue.seq;

    const runId = ++demoTypingRunIdRef.current;

    async function animateTyping() {
      setInput("");
      for (const char of typingContent) {
        if (runId !== demoTypingRunIdRef.current) return;
        setInput((prev) => prev + char);
        await wait(charDelay);
      }
      if (runId !== demoTypingRunIdRef.current) return;
      completeDemoTypingRef.current(targetSessionId, typingSeq);
    }

    void animateTyping();
  }, [demoTypingCue, sessionId]);

  useEffect(() => {
    if (demoStatus !== "complete") return;
    demoTypingRunIdRef.current += 1;
    setInput("");
  }, [demoStatus]);

  const addFiles = useCallback((fileList: FileList | File[]) => {
    if (!sessionId) return;
    setAttachmentError("");
    const incoming = Array.from(fileList);

    setAttachments((prev) => {
      const result = [...prev];
      for (const file of incoming) {
        if (result.length >= MAX_ATTACHMENTS) {
          setAttachmentError(`Maximum ${MAX_ATTACHMENTS} attachments per message.`);
          break;
        }
        if (!ALLOWED_MIMES.has(file.type)) {
          setAttachmentError(`Unsupported file type: ${file.type || file.name}`);
          continue;
        }
        if (file.size > MAX_ATTACHMENT_SIZE) {
          setAttachmentError(`"${file.name}" is too large (max 10 MB).`);
          continue;
        }

        const localId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        const previewUrl = file.type.startsWith("image/")
          ? URL.createObjectURL(file)
          : undefined;
        const pending: PendingAttachment = { localId, file, previewUrl, uploading: true };
        result.push(pending);

        // Fire upload
        api.uploadChatAttachment(sessionId, file)
          .then((res) => {
            setAttachments((current) =>
              current.map((a) =>
                a.localId === localId ? { ...a, uploading: false, uploadedId: res.id } : a,
              ),
            );
          })
          .catch((err) => {
            const msg = err?.message || "Upload failed";
            // Surface the server's detail (e.g. demo size cap) in the banner
            // so the user actually sees *why* — the pending tile only has
            // room for "Failed".
            setAttachmentError(`"${file.name}": ${msg}`);
            setAttachments((current) =>
              current.map((a) =>
                a.localId === localId ? { ...a, uploading: false, error: msg } : a,
              ),
            );
          });
      }
      return result;
    });
  }, [sessionId]);

  const removeAttachment = (localId: string) => {
    setAttachments((prev) => {
      const victim = prev.find((a) => a.localId === localId);
      if (victim?.previewUrl) URL.revokeObjectURL(victim.previewUrl);
      return prev.filter((a) => a.localId !== localId);
    });
  };

  // Drag-and-drop support on the chat area
  const handleDragOver = (e: React.DragEvent) => {
    if (e.dataTransfer.types.includes("Files")) {
      e.preventDefault();
      setIsDragOver(true);
    }
  };
  const handleDragLeave = (e: React.DragEvent) => {
    // Only clear when the drag actually leaves the outer container, not when
    // moving over children. relatedTarget is null or outside when truly leaving.
    const related = e.relatedTarget as Node | null;
    if (!related || !(e.currentTarget as Node).contains(related)) {
      setIsDragOver(false);
    }
  };
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      addFiles(e.dataTransfer.files);
    }
  };

  // Paste image / PDF from clipboard
  const handlePaste = (e: React.ClipboardEvent) => {
    const files: File[] = [];
    for (const item of e.clipboardData.items) {
      if (item.kind === "file") {
        const f = item.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length > 0) {
      e.preventDefault();
      addFiles(files);
    }
  };

  const hasUnfinishedUploads = attachments.some((a) => a.uploading);
  const hasFailedUploads = attachments.some((a) => a.error);
  const readyAttachmentIds = attachments
    .filter((a) => a.uploadedId && !a.error)
    .map((a) => a.uploadedId as string);

  function sendCurrentMessage() {
    const text = input.trim();
    if (!sessionId) return;
    if (!text && readyAttachmentIds.length === 0) return;
    if (hasUnfinishedUploads) return;
    if (!sendMessage(sessionId, text, readyAttachmentIds.length > 0 ? readyAttachmentIds : undefined)) return;
    // Clean up previews
    for (const att of attachments) {
      if (att.previewUrl) URL.revokeObjectURL(att.previewUrl);
    }
    setAttachments([]);
    setAttachmentError("");
    setInput("");
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendCurrentMessage();
    }
  }

  if (!sessionId) {
    return (
      <div className="flex h-full items-center justify-center text-fg-muted">
        Creating chat...
      </div>
    );
  }

  return (
    <div
      className="relative -m-4 flex h-[calc(100%+2rem)] flex-col overflow-hidden md:-m-6 md:h-[calc(100%+3rem)]"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {isDragOver && (
        <div className="pointer-events-none absolute inset-3 z-50 flex items-center justify-center rounded-lg border-2 border-dashed border-brand bg-brand/5 text-sm font-medium text-brand">
          Drop file to attach (images or PDF, max 10 MB each)
        </div>
      )}
      {/* Header with clear button */}
      {messages.length > 0 && (
        <div className="flex justify-end border-b border-line px-4 py-1">
          <button
            onClick={clearCurrentHistory}
            className="text-xs text-fg-muted transition-colors hover:text-red-500"
            disabled={isDemoReplaying}
          >
            Clear chat
          </button>
        </div>
      )}
      {/* Messages */}
      <div
        ref={scrollContainerRef}
        className="min-h-0 flex-1 overflow-y-auto px-4 py-6"
        onScroll={handleMessagesScroll}
      >
        <div className="mx-auto max-w-3xl space-y-4">
          {(canLoadOlder || loadingOlder) && messages.length > 0 && (
            <div className="flex justify-center">
              <button
                onClick={handleLoadOlder}
                disabled={loadingOlder || !canLoadOlder}
                className="rounded-full bg-surface px-3 py-1 text-xs text-fg-muted ring-1 ring-line transition-colors hover:bg-surface-subtle hover:text-fg disabled:cursor-not-allowed disabled:opacity-60"
              >
                {loadingOlder ? "Loading..." : "Load older messages"}
              </button>
            </div>
          )}
          {demoError && (
            <div className="rounded-md bg-rose-50 px-4 py-3 text-sm text-rose-700 ring-1 ring-rose-200">
              {demoError}
            </div>
          )}

          {messages.length === 0 && demoStatus !== "running" && !demoRequested && (
            <div className="py-20 text-center">
              <h3 className="text-lg font-semibold text-fg-muted">
                Lambda ERP Chat
              </h3>
              <p className="mt-2 text-sm text-fg-muted">
                Ask me to create documents, look up data, or run reports.
              </p>
              <div className="mt-6 flex flex-wrap justify-center gap-2">
                {[
                  "What customers do we have?",
                  "Show me the trial balance",
                  "Create a quotation for 10 Bolt Pack M8",
                  "List all unpaid invoices",
                ].map((suggestion) => (
                  <button
                    key={suggestion}
                    onClick={() => {
                      setInput(suggestion);
                      inputRef.current?.focus();
                    }}
                    className="rounded-full bg-surface px-3 py-1.5 text-xs text-fg-muted ring-1 ring-line transition-all hover:bg-surface-subtle hover:text-fg hover:ring-brand/30"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg, idx) => (
            <MessageBubble key={idx} message={msg} />
          ))}
          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Input — bg-surface-muted matches the page so the textarea reads as
           a floating card rather than a strip of UI chrome bolted to the
           bottom. The thin top divider stays as a subtle separator. */}
      <div className="border-t border-line/60 bg-surface-muted px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
        <div className="mx-auto max-w-3xl">
          {/* Attachment preview strip */}
          {attachments.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-2">
              {attachments.map((att) => (
                <div
                  key={att.localId}
                  className="relative flex items-center gap-2 rounded-lg bg-surface px-2 py-1.5 text-xs ring-1 ring-line shadow-card"
                >
                  {att.previewUrl ? (
                    <img src={att.previewUrl} alt="" className="h-10 w-10 rounded object-cover" />
                  ) : (
                    <div className="flex h-10 w-10 items-center justify-center rounded bg-rose-100 text-[10px] font-bold text-rose-700">
                      PDF
                    </div>
                  )}
                  <div className="flex min-w-0 flex-col">
                    <span className="max-w-[160px] truncate text-fg">{att.file.name}</span>
                    <span className="text-[10px] text-fg-muted">
                      {att.uploading ? "Uploading..." : att.error ? "Failed" : `${Math.round(att.file.size / 1024)} KB`}
                    </span>
                  </div>
                  <button
                    onClick={() => removeAttachment(att.localId)}
                    className="ml-1 text-fg-muted transition-colors hover:text-red-500"
                    title="Remove"
                  >
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
                      <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
                    </svg>
                  </button>
                </div>
              ))}
            </div>
          )}
          {attachmentError && (
            <div className="mb-2 rounded-md bg-rose-50 px-3 py-1.5 text-xs text-rose-700 ring-1 ring-rose-200 shadow-card">{attachmentError}</div>
          )}
          <div className="flex items-end gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/gif,image/webp,application/pdf"
              multiple
              className="hidden"
              onChange={(e) => {
                if (e.target.files && e.target.files.length > 0) {
                  addFiles(e.target.files);
                  e.target.value = "";
                }
              }}
            />
            <div className="relative flex-1">
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                onPaste={handlePaste}
                placeholder={
                  isDemoReplaying
                    ? "Running live demo..."
                    : isConnected
                    ? "Type a message..."
                    : "Connecting..."
                }
                disabled={!isConnected || !sessionId || isThinking || isDemoReplaying}
                rows={2}
                className="block w-full resize-none rounded-xl bg-surface px-4 py-2.5 text-sm text-fg ring-1 ring-line shadow-card transition-all placeholder:text-fg-muted/70 focus:outline-none focus:ring-2 focus:ring-brand/30 disabled:bg-surface-subtle disabled:text-fg-muted"
                style={{ minHeight: "3.75rem", maxHeight: "120px" }}
                onInput={(e) => {
                  const target = e.target as HTMLTextAreaElement;
                  target.style.height = "auto";
                  target.style.height = Math.min(Math.max(target.scrollHeight, 60), 120) + "px";
                }}
              />
            </div>
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={!isConnected || !sessionId || isThinking || isDemoReplaying || attachments.length >= MAX_ATTACHMENTS}
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg text-fg-muted transition-all hover:bg-surface-subtle hover:text-fg disabled:cursor-not-allowed disabled:opacity-40 md:h-10 md:w-10"
              title="Attach file (PDF or image, max 10 MB)"
            >
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" />
              </svg>
            </button>
            <button
              onClick={sendCurrentMessage}
              disabled={(!input.trim() && readyAttachmentIds.length === 0) || !isConnected || !sessionId || isThinking || isDemoReplaying || hasUnfinishedUploads || hasFailedUploads}
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-brand text-brand-fg shadow-button-highlight transition-all hover:bg-brand/90 active:translate-y-px disabled:bg-surface-subtle disabled:text-fg-muted disabled:active:translate-y-0 md:h-10 md:w-10"
            >
              <svg
                viewBox="0 0 24 24"
                width="18"
                height="18"
                fill="currentColor"
              >
                <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
              </svg>
            </button>
          </div>
          <div className="mt-1.5 flex items-center gap-1.5">
            <span
              className={`inline-block h-1.5 w-1.5 rounded-full ${isConnected ? "bg-emerald-500" : "bg-rose-500"} ${isConnected ? "" : "animate-pulse"}`}
            />
            <span className="text-[11px] text-fg-muted">
              {isConnected ? "Connected" : connectionStatus === "connecting" ? "Connecting..." : "Disconnected"}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

function AttachmentThumbs({ attachments }: { attachments?: ChatAttachment[] }) {
  if (!attachments || attachments.length === 0) return null;
  return (
    <div className="mt-1 flex flex-wrap gap-1.5">
      {attachments.map((att) => {
        const url = api.getChatAttachmentUrl(att.id);
        const isImage = att.mime_type.startsWith("image/");
        return (
          <a
            key={att.id}
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 rounded bg-black/10 px-1.5 py-1 text-[11px] hover:bg-black/20"
            title={att.filename}
          >
            {isImage ? (
              <img src={url} alt={att.filename} className="h-8 w-8 rounded object-cover" />
            ) : (
              <span className="flex h-8 w-8 items-center justify-center rounded bg-red-200 text-[9px] font-bold text-red-800">PDF</span>
            )}
            <span className="max-w-[120px] truncate">{att.filename}</span>
          </a>
        );
      })}
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  if (message.type === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] break-words rounded-2xl rounded-br-md bg-gradient-to-b from-brand to-brand/90 px-4 py-2.5 text-sm text-brand-fg ring-1 ring-black/10 shadow-bubble-user">
          {message.content}
          <AttachmentThumbs attachments={message.attachments} />
          {message.timestamp && (
            <div className="mt-1 text-right text-[10px] text-brand-fg/70" title={formatFullTimestamp(message.timestamp)}>{formatTime(message.timestamp)}</div>
          )}
        </div>
      </div>
    );
  }

  if (message.type === "assistant") {
    return (
      <div className="flex justify-start">
        <div className="max-w-[80%] break-words rounded-2xl rounded-bl-md bg-surface px-4 py-2.5 text-sm text-fg ring-1 ring-line shadow-card">
          <MarkdownContent content={message.content} />
          {message.timestamp && (
            <div className="mt-1 text-right text-[10px] text-fg-muted" title={formatFullTimestamp(message.timestamp)}>{formatTime(message.timestamp)}</div>
          )}
        </div>
      </div>
    );
  }

  if (message.type === "thinking") {
    return (
      <div className="flex justify-start">
        <div className="flex items-center gap-2 rounded-full bg-surface px-3 py-1.5 text-xs text-fg-muted ring-1 ring-line shadow-card">
          <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-brand" />
          {message.content}
          {message.provider && (
            <span className="rounded bg-surface-subtle px-1.5 py-0.5 text-[10px] font-medium text-fg-muted ring-1 ring-line">
              {message.provider}
              {message.model ? ` · ${message.model}` : ""}
            </span>
          )}
        </div>
      </div>
    );
  }

  if (message.type === "tool_call") {
    return (
      <div className="flex justify-start">
        <div className="rounded-lg bg-surface px-3 py-2 text-xs ring-1 ring-line shadow-card">
          <div className="flex items-center gap-1.5 font-medium text-fg-muted">
            <svg
              viewBox="0 0 24 24"
              width="12"
              height="12"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
            </svg>
            <span className="font-mono">{message.tool}</span>
          </div>
          {message.args && (
            <pre className="mt-1 max-h-20 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-fg-muted/80">
              {JSON.stringify(message.args, null, 2)}
            </pre>
          )}
        </div>
      </div>
    );
  }

  if (message.type === "tool_result") {
    return (
      <div className="flex justify-start">
        <div
          className={`rounded-lg px-3 py-2 text-xs shadow-card ring-1 ring-inset ${message.success ? "bg-emerald-50 ring-emerald-200" : "bg-rose-50 ring-rose-200"}`}
        >
          <div className="flex items-center gap-1.5">
            <span className={message.success ? "text-emerald-600" : "text-rose-600"}>
              {message.success ? "\u2713" : "\u2717"}
            </span>
            <span className="font-mono font-medium text-fg">{message.tool}</span>
          </div>
          <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-fg-muted">
            {message.content}
          </pre>
        </div>
      </div>
    );
  }

  if (message.type === "error") {
    return (
      <div className="flex justify-start">
        <div className="max-w-[80%] rounded-2xl rounded-bl-md bg-rose-50 px-4 py-2.5 text-sm text-rose-700 ring-1 ring-rose-200 shadow-card">
          {message.content}
        </div>
      </div>
    );
  }

  return null;
}

function MarkdownContent({ content }: { content: string }) {
  const lines = content.split("\n");
  const elements: React.ReactNode[] = [];
  let inCodeBlock = false;
  let codeLines: string[] = [];

  lines.forEach((line, i) => {
    if (line.startsWith("```")) {
      if (inCodeBlock) {
        elements.push(
          <pre
            key={`code-${i}`}
            className="my-2 overflow-auto rounded bg-gray-800 p-3 text-xs text-green-300"
          >
            {codeLines.join("\n")}
          </pre>,
        );
        codeLines = [];
        inCodeBlock = false;
      } else {
        inCodeBlock = true;
      }
      return;
    }
    if (inCodeBlock) {
      codeLines.push(line);
      return;
    }

    if (line.startsWith("### ")) {
      elements.push(
        <h4 key={i} className="mt-3 mb-1 font-semibold">
          {line.slice(4)}
        </h4>,
      );
    } else if (line.startsWith("## ")) {
      elements.push(
        <h3 key={i} className="mt-3 mb-1 text-base font-semibold">
          {line.slice(3)}
        </h3>,
      );
    } else if (/^\s*[-*•]\s?/.test(line)) {
      const content = line.replace(/^\s*[-*•]\s?/, "");
      elements.push(
        <div key={i} className="ml-3 flex gap-1">
          <span className="text-fg-muted">&bull;</span>
          <span>{formatInline(content)}</span>
        </div>,
      );
    } else if (line.trim() === "") {
      elements.push(<div key={i} className="h-2" />);
    } else {
      elements.push(
        <p key={i} className="leading-relaxed">
          {formatInline(line)}
        </p>,
      );
    }
  });

  return <div>{elements}</div>;
}

function renderLink(href: string, label: React.ReactNode, key: number | string): React.ReactNode {
  // `/api/*` is a backend endpoint (PDFs, file downloads, attachments) —
  // not a React Router route. Route client-side only for real SPA paths.
  const isSpaRoute = href.startsWith("/") && !href.startsWith("/api/");
  const baseClass = "font-medium text-brand underline decoration-brand/40 underline-offset-2 transition-colors hover:decoration-brand";
  if (isSpaRoute) {
    return (
      <Link key={key} to={href} className={baseClass}>
        {label}
      </Link>
    );
  }
  return (
    <a
      key={key}
      href={href}
      className={baseClass}
      target="_blank"
      rel="noopener noreferrer"
    >
      {label}
    </a>
  );
}

// Bare URLs: http(s)://... or /internal?query paths. We stop at whitespace,
// closing punctuation like ), ], }, ", ', and end-of-string. Trailing
// sentence punctuation (. , ; : ! ?) is stripped below so it doesn't get
// swallowed into the href.
const BARE_URL_RE = /(https?:\/\/[^\s)\]}"'<>]+|\/(?:reports|app|masters|admin|chat|setup|tutorial)[^\s)\]}"'<>]*)/g;

function formatInline(text: string): React.ReactNode {
  // Split on (in priority order) markdown links, bold, inline code, bare URLs.
  const parts = text.split(/(\[[^\]]+\]\([^)]+\)|\*\*[^*]+?\*\*|`[^`]+`)/g);
  return parts.flatMap((part, i) => {
    const linkMatch = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (linkMatch) {
      const [, label, href] = linkMatch;
      return renderLink(href, formatInline(label), i);
    }
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return (
        <strong key={i} className="font-semibold">
          {formatInline(part.slice(2, -2))}
        </strong>
      );
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code
          key={i}
          className="rounded bg-gray-200 px-1 py-0.5 text-xs font-mono"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    // Plain text segment: scan for bare URLs and linkify them.
    return linkifyBare(part, `${i}`);
  });
}

function linkifyBare(text: string, keyPrefix: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  const re = new RegExp(BARE_URL_RE.source, "g");
  let idx = 0;
  while ((match = re.exec(text)) !== null) {
    const start = match.index;
    let matched = match[0];
    // Strip trailing sentence punctuation that shouldn't belong to the URL.
    let trailing = "";
    while (matched.length > 0 && /[.,;:!?]$/.test(matched)) {
      trailing = matched.slice(-1) + trailing;
      matched = matched.slice(0, -1);
    }
    if (start > lastIndex) {
      nodes.push(text.slice(lastIndex, start));
    }
    nodes.push(renderLink(matched, matched, `${keyPrefix}-u${idx}`));
    if (trailing) nodes.push(trailing);
    lastIndex = start + match[0].length;
    idx += 1;
  }
  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }
  return nodes.length > 0 ? nodes : [text];
}
