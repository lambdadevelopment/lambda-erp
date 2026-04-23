import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";

export interface ChatSession {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  last_message_at?: string | null;
}

export interface ChatAttachment {
  id: string;
  filename: string;
  mime_type: string;
}

export interface ChatMessage {
  id?: number;
  type: "user" | "assistant" | "thinking" | "tool_call" | "tool_result" | "error";
  content: string;
  tool?: string;
  args?: Record<string, unknown>;
  success?: boolean;
  iteration?: number;
  timestamp?: string;
  attachments?: ChatAttachment[];
  provider?: string;
  model?: string;
}

type ConnectionStatus = "connecting" | "connected" | "disconnected";

interface NavigationFlash {
  group: string;
  item?: string;
  key: number;
}

interface PersistedChatMessage {
  id?: number;
  role: string;
  content: string;
  created_at?: string;
  attachments?: ChatAttachment[];
}

interface ChatContextValue {
  sessions: ChatSession[];
  connectionStatus: ConnectionStatus;
  connectionVersion: number;
  navigationFlash: NavigationFlash | null;
  createSession: () => Promise<ChatSession>;
  startDemo: (sessionId: string) => Promise<void>;
  completeDemoTyping: (sessionId: string, seq: number) => void;
  deleteSession: (sessionId: string) => Promise<void>;
  loadHistory: (sessionId: string) => Promise<void>;
  loadMoreHistory: (sessionId: string) => Promise<void>;
  clearHistory: (sessionId: string) => Promise<void>;
  sendMessage: (sessionId: string, content: string, attachmentIds?: string[]) => boolean;
  getMessages: (sessionId?: string) => ChatMessage[];
  hasMoreHistory: (sessionId?: string) => boolean;
  isLoadingOlder: (sessionId?: string) => boolean;
  isThinking: (sessionId?: string) => boolean;
  getDemoStatus: (sessionId?: string) => "idle" | "running" | "complete";
  getDemoTypingCue: (sessionId?: string) => { content: string; seq: number; typingMs: number } | null;
}

interface PendingRequest {
  expectedType: string;
  resolve: (value: Record<string, unknown>) => void;
  reject: (error: Error) => void;
}

const ChatContext = createContext<ChatContextValue | null>(null);

const NAV_FLASH_MAP: Record<string, { group: string; item: string }> = {
  "quotation": { group: "Selling", item: "Quotation" },
  "sales-order": { group: "Selling", item: "Sales Order" },
  "sales-invoice": { group: "Selling", item: "Sales Invoice" },
  "pos-invoice": { group: "Selling", item: "POS Invoice" },
  "purchase-order": { group: "Buying", item: "Purchase Order" },
  "purchase-invoice": { group: "Buying", item: "Purchase Invoice" },
  "payment-entry": { group: "Accounting", item: "Payment Entry" },
  "journal-entry": { group: "Accounting", item: "Journal Entry" },
  "bank-transaction": { group: "Accounting", item: "Bank Transaction" },
  "budget": { group: "Accounting", item: "Budget" },
  "subscription": { group: "Accounting", item: "Subscription" },
  "stock-entry": { group: "Stock", item: "Stock Entry" },
  "delivery-note": { group: "Stock", item: "Delivery Note" },
  "purchase-receipt": { group: "Stock", item: "Purchase Receipt" },
  "pricing-rule": { group: "Settings", item: "Pricing Rule" },
};

const MASTER_FLASH_MAP: Record<string, { group: string; item: string }> = {
  "customer": { group: "Masters", item: "Customer" },
  "supplier": { group: "Masters", item: "Supplier" },
  "item": { group: "Masters", item: "Item" },
  "warehouse": { group: "Masters", item: "Warehouse" },
  "company": { group: "Masters", item: "Company" },
};

function normalizeDoctype(value: unknown): string | null {
  if (typeof value !== "string") return null;
  return value.trim().toLowerCase().replace(/\s+/g, "-");
}

function getNavigationFlashFromTool(tool: string, args?: Record<string, unknown>): { group: string; item: string } | null {
  // Custom analytics tools — flash the sidebar group without targeting an item.
  // (There's no fixed item for custom analytics; the newly touched draft
  //  shows up as an entry under the group itself.)
  if (
    tool === "create_custom_analytics_report" ||
    tool === "update_custom_analytics_report"
  ) {
    return { group: "Custom Analytics", item: "" };
  }

  if (!args) return null;

  // Document tools
  let doctype: string | null = null;
  if (
    tool === "create_document" ||
    tool === "update_document" ||
    tool === "submit_document" ||
    tool === "cancel_document"
  ) {
    doctype = normalizeDoctype(args.doctype);
  } else if (tool === "convert_document") {
    doctype = normalizeDoctype(args.target_doctype);
  }
  if (doctype && NAV_FLASH_MAP[doctype]) {
    return NAV_FLASH_MAP[doctype];
  }

  // Master tools
  if (tool === "create_master" || tool === "update_master") {
    const masterType = normalizeDoctype(args.master_type);
    if (masterType && MASTER_FLASH_MAP[masterType]) {
      return MASTER_FLASH_MAP[masterType];
    }
  }

  return null;
}

function mapPersistedMessage(message: PersistedChatMessage): ChatMessage {
  return {
    id: message.id,
    type: message.role === "user" ? "user" : "assistant",
    content: message.content,
    timestamp: message.created_at,
    attachments: message.attachments,
  };
}

function isPersistentMessage(message: ChatMessage): boolean {
  return message.type === "user" || message.type === "assistant";
}

// Per-browser memory of chat sessions this visitor has created or opened.
// Needed because the `public_manager` demo account is shared across all
// visitors and the server-side list filter hides demo-only chats to prevent
// pile-up — without this, a visitor's freshly-created demo chat would
// vanish from the sidebar on page reload.
const KNOWN_SESSIONS_KEY = "lambda-erp:known-chat-sessions";

function getKnownSessionIds(): Set<string> {
  if (typeof localStorage === "undefined") return new Set();
  try {
    const raw = localStorage.getItem(KNOWN_SESSIONS_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    return new Set(Array.isArray(parsed) ? parsed.filter((v) => typeof v === "string") : []);
  } catch {
    return new Set();
  }
}

function writeKnownSessionIds(ids: Set<string>) {
  if (typeof localStorage === "undefined") return;
  try {
    localStorage.setItem(KNOWN_SESSIONS_KEY, JSON.stringify([...ids]));
  } catch {
    // ignore storage errors (quota / private mode)
  }
}

export function rememberSessionId(id: string) {
  const ids = getKnownSessionIds();
  if (ids.has(id)) return;
  ids.add(id);
  writeKnownSessionIds(ids);
}

function forgetSessionId(id: string) {
  const ids = getKnownSessionIds();
  if (!ids.delete(id)) return;
  writeKnownSessionIds(ids);
}

function makeRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `req-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function toSortTime(timestamp?: string): number {
  if (!timestamp) return 0;
  // Normalize SQLite's space-separated format ("YYYY-MM-DD HH:MM:SS") to
  // ISO T-form so it parses consistently with Python `now()` output.
  const s = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/.test(timestamp)
    ? timestamp.replace(" ", "T")
    : timestamp;
  const value = new Date(s).getTime();
  return Number.isNaN(value) ? 0 : value;
}

export function ChatProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [messagesBySession, setMessagesBySession] = useState<Record<string, ChatMessage[]>>({});
  const [hasMoreBySession, setHasMoreBySession] = useState<Record<string, boolean>>({});
  const [oldestIdBySession, setOldestIdBySession] = useState<Record<string, number | null>>({});
  const [loadingOlderBySession, setLoadingOlderBySession] = useState<Record<string, boolean>>({});
  const [thinkingBySession, setThinkingBySession] = useState<Record<string, boolean>>({});
  const [demoStatusBySession, setDemoStatusBySession] = useState<Record<string, "idle" | "running" | "complete">>({});
  const [demoTypingCueBySession, setDemoTypingCueBySession] = useState<Record<string, { content: string; seq: number; typingMs: number } | null>>({});
  const [navigationFlash, setNavigationFlash] = useState<NavigationFlash | null>(null);
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>("connecting");
  const [connectionVersion, setConnectionVersion] = useState(0);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const pendingRequestsRef = useRef<Map<string, PendingRequest>>(new Map());
  const pendingCreateSessionRef = useRef<Promise<ChatSession> | null>(null);
  const lastMessageAtRef = useRef<Record<string, string>>({});
  const pendingToolCallsRef = useRef<Record<string, { tool: string; args?: Record<string, unknown> } | null>>({});
  const navFlashTimeoutRef = useRef<number | null>(null);

  function rejectPendingRequests(message: string) {
    for (const pending of pendingRequestsRef.current.values()) {
      pending.reject(new Error(message));
    }
    pendingRequestsRef.current.clear();
  }

  function updateSessionMessages(sessionId: string, updater: (messages: ChatMessage[]) => ChatMessage[]) {
    setMessagesBySession((prev) => ({
      ...prev,
      [sessionId]: updater(prev[sessionId] ?? []),
    }));
  }

  function setSessionThinking(sessionId: string, value: boolean) {
    setThinkingBySession((prev) => ({ ...prev, [sessionId]: value }));
  }

  function setSessionDemoStatus(sessionId: string, value: "idle" | "running" | "complete") {
    setDemoStatusBySession((prev) => ({ ...prev, [sessionId]: value }));
  }

  function setSessionDemoTypingCue(sessionId: string, value: { content: string; seq: number; typingMs: number } | null) {
    setDemoTypingCueBySession((prev) => ({ ...prev, [sessionId]: value }));
  }

  function triggerNavigationFlash(group: string, item?: string) {
    if (navFlashTimeoutRef.current !== null) {
      window.clearTimeout(navFlashTimeoutRef.current);
    }
    setNavigationFlash({ group, item, key: Date.now() });
    navFlashTimeoutRef.current = window.setTimeout(() => {
      setNavigationFlash((prev) => (prev?.group === group && prev?.item === item ? null : prev));
      navFlashTimeoutRef.current = null;
    }, 1100);
  }

  function sortSessionsByMessageActivity(sessionList: ChatSession[]): ChatSession[] {
    return [...sessionList].sort((left, right) => {
      const leftTime =
        toSortTime(lastMessageAtRef.current[left.id])
        || toSortTime(left.last_message_at || undefined)
        || toSortTime(left.created_at);
      const rightTime =
        toSortTime(lastMessageAtRef.current[right.id])
        || toSortTime(right.last_message_at || undefined)
        || toSortTime(right.created_at);
      return rightTime - leftTime;
    });
  }

  function updateSessionList(updater: (sessions: ChatSession[]) => ChatSession[]) {
    setSessions((prev) => sortSessionsByMessageActivity(updater(prev)));
  }

  function recordSessionMessage(sessionId: string, timestamp?: string) {
    const nextTimestamp = timestamp || new Date().toISOString();
    const currentTimestamp = lastMessageAtRef.current[sessionId];
    if (toSortTime(nextTimestamp) >= toSortTime(currentTimestamp)) {
      lastMessageAtRef.current = {
        ...lastMessageAtRef.current,
        [sessionId]: nextTimestamp,
      };
    }
  }

  function hydrateLastMessageTimes(sessionList: ChatSession[]) {
    const next = { ...lastMessageAtRef.current };
    for (const session of sessionList) {
      if (session.last_message_at && !next[session.id]) {
        next[session.id] = session.last_message_at;
      }
    }
    lastMessageAtRef.current = next;
  }

  function sendRaw(payload: Record<string, unknown>) {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      throw new Error("Chat is disconnected.");
    }
    ws.send(JSON.stringify(payload));
  }

  function sendRequest(type: string, payload: Record<string, unknown>, expectedType: string): Promise<Record<string, unknown>> {
    return new Promise((resolve, reject) => {
      const requestId = makeRequestId();
      pendingRequestsRef.current.set(requestId, {
        expectedType,
        resolve,
        reject,
      });

      try {
        sendRaw({ type, request_id: requestId, ...payload });
      } catch (error) {
        pendingRequestsRef.current.delete(requestId);
        reject(error instanceof Error ? error : new Error("Chat request failed."));
      }
    });
  }

  useEffect(() => {
    let disposed = false;

    function connect() {
      if (disposed) return;

      setConnectionStatus("connecting");

      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const wsUrl = `${protocol}//${window.location.host}/ws/chat`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        if (disposed) {
          ws.close();
          return;
        }
        setConnectionStatus("connected");
        setConnectionVersion((prev) => prev + 1);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as Record<string, unknown>;
          const requestId = typeof data.request_id === "string" ? data.request_id : null;
          const eventType = typeof data.type === "string" ? data.type : null;
          const sessionId = typeof data.session_id === "string" ? data.session_id : null;

          if (requestId) {
            const pending = pendingRequestsRef.current.get(requestId);
            if (pending) {
              if (eventType === "error") {
                pendingRequestsRef.current.delete(requestId);
                pending.reject(new Error(String(data.content || "Request failed.")));
              } else if (eventType === pending.expectedType) {
                pendingRequestsRef.current.delete(requestId);
                pending.resolve(data);
              }
            }
          }

          if (eventType === "sessions_list") {
            const nextSessions = Array.isArray(data.sessions) ? (data.sessions as ChatSession[]) : [];
            hydrateLastMessageTimes(nextSessions);
            setSessions(
              sortSessionsByMessageActivity(
                nextSessions,
              ),
            );
            // Merge in any "mine" sessions (tracked per-browser in
            // localStorage) that the server hid from the list because
            // they're demo-only on a shared public_manager account.
            const serverIds = new Set(nextSessions.map((s) => s.id));
            const knownIds = getKnownSessionIds();
            const missing = [...knownIds].filter((id) => !serverIds.has(id));
            if (missing.length > 0) {
              void Promise.all(
                missing.map(async (id) => {
                  try {
                    const row = await api.getChatSession(id);
                    if (!row || row.detail || !row.id) {
                      forgetSessionId(id);
                      return null;
                    }
                    return row as ChatSession;
                  } catch {
                    return null;
                  }
                }),
              ).then((fetched) => {
                const resolved = fetched.filter((s): s is ChatSession => !!s);
                if (!resolved.length) return;
                hydrateLastMessageTimes(resolved);
                updateSessionList((prev) => [
                  ...resolved.filter((s) => !prev.some((p) => p.id === s.id)),
                  ...prev,
                ]);
              });
            }
            return;
          }

          if (eventType === "session_created") {
            const session = data.session as ChatSession | undefined;
            if (!session) return;
            rememberSessionId(session.id);
            hydrateLastMessageTimes([session]);
            updateSessionList((prev) => [session, ...prev.filter((entry) => entry.id !== session.id)]);
            return;
          }

          if (eventType === "session_deleted" && sessionId) {
            forgetSessionId(sessionId);
            updateSessionList((prev) => prev.filter((session) => session.id !== sessionId));
            setMessagesBySession((prev) => {
              const next = { ...prev };
              delete next[sessionId];
              return next;
            });
            setThinkingBySession((prev) => {
              const next = { ...prev };
              delete next[sessionId];
              return next;
            });
            setDemoStatusBySession((prev) => {
              const next = { ...prev };
              delete next[sessionId];
              return next;
            });
            setDemoTypingCueBySession((prev) => {
              const next = { ...prev };
              delete next[sessionId];
              return next;
            });
            setHasMoreBySession((prev) => {
              const next = { ...prev };
              delete next[sessionId];
              return next;
            });
            setOldestIdBySession((prev) => {
              const next = { ...prev };
              delete next[sessionId];
              return next;
            });
            setLoadingOlderBySession((prev) => {
              const next = { ...prev };
              delete next[sessionId];
              return next;
            });
            const nextLastMessageAt = { ...lastMessageAtRef.current };
            delete nextLastMessageAt[sessionId];
            lastMessageAtRef.current = nextLastMessageAt;
            return;
          }

          if (eventType === "history_loaded" && sessionId) {
            const persisted = Array.isArray(data.messages)
              ? (data.messages as PersistedChatMessage[]).map(mapPersistedMessage)
              : [];
            const isPrepend = data.before_id != null;
            const hasMore = Boolean(data.has_more);
            const oldestId =
              typeof data.oldest_id === "number" ? data.oldest_id : null;

            setMessagesBySession((prev) => {
              if (isPrepend) {
                const existing = prev[sessionId] ?? [];
                const existingIds = new Set(
                  existing
                    .map((entry) => entry.id)
                    .filter((id): id is number => typeof id === "number"),
                );
                const filtered = persisted.filter(
                  (entry) => entry.id === undefined || !existingIds.has(entry.id),
                );
                return { ...prev, [sessionId]: [...filtered, ...existing] };
              }
              return { ...prev, [sessionId]: persisted };
            });

            setHasMoreBySession((prev) => ({ ...prev, [sessionId]: hasMore }));
            setOldestIdBySession((prev) => ({ ...prev, [sessionId]: oldestId }));
            setLoadingOlderBySession((prev) => ({ ...prev, [sessionId]: false }));

            const title = typeof data.title === "string" ? data.title : null;
            if (title) {
              updateSessionList((prev) =>
                prev.map((session) => (
                  session.id === sessionId ? { ...session, title } : session
                )),
              );
            }

            if (!isPrepend) {
              const lastPersistedMessage = persisted[persisted.length - 1];
              if (lastPersistedMessage?.timestamp) {
                recordSessionMessage(sessionId, lastPersistedMessage.timestamp);
                updateSessionList((prev) => prev);
              }
            }
            return;
          }

          if (eventType === "history_cleared" && sessionId) {
            setMessagesBySession((prev) => ({ ...prev, [sessionId]: [] }));
            setHasMoreBySession((prev) => ({ ...prev, [sessionId]: false }));
            setOldestIdBySession((prev) => ({ ...prev, [sessionId]: null }));
            setLoadingOlderBySession((prev) => ({ ...prev, [sessionId]: false }));
            setSessionThinking(sessionId, false);
            setSessionDemoStatus(sessionId, "idle");
            setSessionDemoTypingCue(sessionId, null);
            return;
          }

          if (eventType === "message_added" && sessionId) {
            const message = data.message as PersistedChatMessage | undefined;
            if (!message) return;
            recordSessionMessage(sessionId, message.created_at);
            if (message.role === "user") {
              setSessionDemoTypingCue(sessionId, null);
            }
            updateSessionMessages(sessionId, (prev) => [
              ...prev.filter((entry) => entry.type !== "thinking"),
              mapPersistedMessage(message),
            ]);
            updateSessionList((prev) => prev);
            return;
          }

          if (eventType === "demo_started" && sessionId) {
            setSessionDemoStatus(sessionId, "running");
            setSessionDemoTypingCue(sessionId, null);
            return;
          }

          if (eventType === "demo_typing" && sessionId) {
            setSessionDemoStatus(sessionId, "running");
            setSessionDemoTypingCue(sessionId, {
              content: String(data.content || ""),
              seq: Math.max(0, Number(data.seq || 0)),
              typingMs: Math.max(0, Number(data.typing_ms || 0)),
            });
            return;
          }

          if (eventType === "demo_replay_complete" && sessionId) {
            setSessionDemoStatus(sessionId, "complete");
            setSessionDemoTypingCue(sessionId, null);
            return;
          }

          if (eventType === "navigation_flash") {
            const group = typeof data.group === "string" ? data.group : "";
            const item = typeof data.item === "string" ? data.item : undefined;
            if (group) {
              triggerNavigationFlash(group, item);
            }
            return;
          }

          if (eventType === "session_title_updated" && sessionId) {
            const title = typeof data.title === "string" ? data.title : "";
            updateSessionList((prev) =>
              prev.map((session) => (
                session.id === sessionId ? { ...session, title } : session
              )),
            );
            return;
          }

          if (eventType === "thinking" && sessionId) {
            setSessionThinking(sessionId, true);
            updateSessionMessages(sessionId, (prev) => {
              const prior = prev.find((m) => m.type === "thinking");
              return [
                ...prev.filter((message) => message.type !== "thinking"),
                {
                  type: "thinking",
                  content: `Thinking (step ${data.iteration})...`,
                  provider: prior?.provider,
                  model: prior?.model,
                },
              ];
            });
            return;
          }

          if (eventType === "llm_provider" && sessionId) {
            const provider = typeof data.provider === "string" ? data.provider : undefined;
            const model = typeof data.model === "string" ? data.model : undefined;
            const role = typeof data.role === "string" ? data.role : undefined;
            const content = role === "code_specialist"
              ? "Delegating to code specialist..."
              : undefined;
            setSessionThinking(sessionId, true);
            updateSessionMessages(sessionId, (prev) => {
              const hasThinking = prev.some((m) => m.type === "thinking");
              if (hasThinking) {
                return prev.map((message) => (
                  message.type === "thinking"
                    ? {
                        ...message,
                        provider,
                        model,
                        ...(content ? { content } : {}),
                      }
                    : message
                ));
              }
              // No active thinking bubble (e.g. we're mid-tool_call and the
              // backend just handed off to the code specialist). Add one so
              // the provider handoff is visible in the UI.
              return [
                ...prev,
                {
                  type: "thinking",
                  content: content ?? "Thinking...",
                  provider,
                  model,
                },
              ];
            });
            return;
          }

          if (eventType === "tool_call" && sessionId) {
            pendingToolCallsRef.current = {
              ...pendingToolCallsRef.current,
              [sessionId]: {
                tool: String(data.tool || ""),
                args: data.args as Record<string, unknown> | undefined,
              },
            };
            updateSessionMessages(sessionId, (prev) => [
              ...prev.filter((message) => message.type !== "thinking"),
              {
                type: "tool_call",
                content: `Calling ${data.tool}...`,
                tool: data.tool as string,
                args: data.args as Record<string, unknown>,
              },
            ]);
            return;
          }

          if (eventType === "tool_result" && sessionId) {
            const pendingToolCall = pendingToolCallsRef.current[sessionId];
            if (pendingToolCall && pendingToolCall.tool === String(data.tool || "") && Boolean(data.success)) {
              const isCustomAnalyticsTool =
                pendingToolCall.tool === "create_custom_analytics_report" ||
                pendingToolCall.tool === "update_custom_analytics_report";
              if (isCustomAnalyticsTool) {
                // Prefer the id the backend just committed (covers create);
                // fall back to the tool's own args (covers update with an
                // unreturned id). The item value becomes the draft id so the
                // sidebar can highlight that specific row.
                const backendReportId = typeof data.report_id === "string" ? data.report_id : undefined;
                const argReportId = typeof pendingToolCall.args?.report_id === "string"
                  ? (pendingToolCall.args.report_id as string)
                  : undefined;
                const reportId = backendReportId || argReportId || "";
                triggerNavigationFlash("Custom Analytics", reportId);
                queryClient.invalidateQueries({ queryKey: ["runtime-drafts"] });
              } else {
                const flashTarget = getNavigationFlashFromTool(pendingToolCall.tool, pendingToolCall.args);
                if (flashTarget) {
                  triggerNavigationFlash(flashTarget.group, flashTarget.item);
                }
              }
            }
            pendingToolCallsRef.current = {
              ...pendingToolCallsRef.current,
              [sessionId]: null,
            };
            updateSessionMessages(sessionId, (prev) => [
              ...prev,
              {
                type: "tool_result",
                content: String(data.summary || ""),
                tool: data.tool as string,
                success: Boolean(data.success),
              },
            ]);
            return;
          }

          if (eventType === "complete" && sessionId) {
            setSessionThinking(sessionId, false);
            updateSessionMessages(sessionId, (prev) => prev.filter((message) => message.type !== "thinking"));
            return;
          }

          if (eventType === "error") {
            if (requestId) {
              return;
            }

            if (sessionId) {
              setSessionThinking(sessionId, false);
              updateSessionMessages(sessionId, (prev) => [
                ...prev.filter((message) => message.type !== "thinking"),
                { type: "error", content: String(data.content || "Chat error.") },
              ]);
            }
          }
        } catch {
          // Ignore malformed socket events.
        }
      };

      ws.onerror = () => {
        ws.close();
      };

      ws.onclose = (event) => {
        if (wsRef.current === ws) {
          wsRef.current = null;
        }
        rejectPendingRequests("Chat disconnected.");
        setConnectionStatus("disconnected");
        if (disposed) return;
        if (event.code === 4001 || event.code === 4003) {
          window.location.href = "/login";
          return;
        }
        reconnectTimerRef.current = window.setTimeout(connect, 3000);
      };
    }

    connect();

    return () => {
      disposed = true;
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
      }
      if (navFlashTimeoutRef.current !== null) {
        window.clearTimeout(navFlashTimeoutRef.current);
      }
      rejectPendingRequests("Chat disconnected.");
      if (wsRef.current) {
        const ws = wsRef.current;
        wsRef.current = null;
        ws.close();
      }
    };
  }, []);

  async function createSession(): Promise<ChatSession> {
    if (pendingCreateSessionRef.current) {
      return pendingCreateSessionRef.current;
    }

    const promise = sendRequest("create_session", {}, "session_created")
      .then((data) => data.session as ChatSession)
      .finally(() => {
        if (pendingCreateSessionRef.current === promise) {
          pendingCreateSessionRef.current = null;
        }
      });

    pendingCreateSessionRef.current = promise;
    return promise;
  }

  async function startDemo(sessionId: string): Promise<void> {
    setSessionDemoStatus(sessionId, "running");
    setSessionDemoTypingCue(sessionId, null);
    try {
      await sendRequest("start_demo", { session_id: sessionId }, "demo_started");
    } catch (error) {
      setSessionDemoStatus(sessionId, "idle");
      throw error;
    }
  }

  function completeDemoTyping(sessionId: string, seq: number): void {
    try {
      sendRaw({ type: "demo_typing_done", session_id: sessionId, seq });
    } catch {
      // Ignore dropped acknowledgements; backend has a timeout fallback.
    }
  }

  async function deleteSession(sessionId: string): Promise<void> {
    await sendRequest("delete_session", { session_id: sessionId }, "session_deleted");
  }

  async function loadHistory(sessionId: string): Promise<void> {
    await sendRequest("load_history", { session_id: sessionId }, "history_loaded");
  }

  async function loadMoreHistory(sessionId: string): Promise<void> {
    if (loadingOlderBySession[sessionId]) return;
    const oldestId = oldestIdBySession[sessionId];
    if (oldestId == null) return;
    if (!hasMoreBySession[sessionId]) return;
    setLoadingOlderBySession((prev) => ({ ...prev, [sessionId]: true }));
    try {
      await sendRequest(
        "load_history",
        { session_id: sessionId, before_id: oldestId },
        "history_loaded",
      );
    } catch (error) {
      setLoadingOlderBySession((prev) => ({ ...prev, [sessionId]: false }));
      throw error;
    }
  }

  async function clearHistory(sessionId: string): Promise<void> {
    await sendRequest("clear_history", { session_id: sessionId }, "history_cleared");
  }

  function sendMessage(sessionId: string, content: string, attachmentIds?: string[]): boolean {
    const trimmed = content.trim();
    const hasAttachments = (attachmentIds?.length ?? 0) > 0;
    if (!trimmed && !hasAttachments) return false;

    try {
      setSessionThinking(sessionId, true);
      const payload: Record<string, unknown> = {
        type: "send_message",
        session_id: sessionId,
        content: trimmed,
      };
      if (hasAttachments) payload.attachment_ids = attachmentIds;
      sendRaw(payload);
      return true;
    } catch {
      setSessionThinking(sessionId, false);
      return false;
    }
  }

  function getMessages(sessionId?: string): ChatMessage[] {
    if (!sessionId) return [];
    return messagesBySession[sessionId] ?? [];
  }

  function isThinking(sessionId?: string): boolean {
    if (!sessionId) return false;
    return Boolean(thinkingBySession[sessionId]);
  }

  function hasMoreHistory(sessionId?: string): boolean {
    if (!sessionId) return false;
    return Boolean(hasMoreBySession[sessionId]);
  }

  function isLoadingOlder(sessionId?: string): boolean {
    if (!sessionId) return false;
    return Boolean(loadingOlderBySession[sessionId]);
  }

  function getDemoStatus(sessionId?: string): "idle" | "running" | "complete" {
    if (!sessionId) return "idle";
    return demoStatusBySession[sessionId] ?? "idle";
  }

  function getDemoTypingCue(sessionId?: string): { content: string; seq: number; typingMs: number } | null {
    if (!sessionId) return null;
    return demoTypingCueBySession[sessionId] ?? null;
  }

  return (
    <ChatContext.Provider
      value={{
        sessions,
        connectionStatus,
        connectionVersion,
        navigationFlash,
        createSession,
        startDemo,
        completeDemoTyping,
        deleteSession,
        loadHistory,
        loadMoreHistory,
        clearHistory,
        sendMessage,
        getMessages,
        hasMoreHistory,
        isLoadingOlder,
        isThinking,
        getDemoStatus,
        getDemoTypingCue,
      }}
    >
      {children}
    </ChatContext.Provider>
  );
}

export function useChat(): ChatContextValue {
  const context = useContext(ChatContext);
  if (!context) {
    throw new Error("useChat must be used within ChatProvider.");
  }
  return context;
}
