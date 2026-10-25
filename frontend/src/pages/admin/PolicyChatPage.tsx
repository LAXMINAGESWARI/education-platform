import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import {
  createChat,
  deleteChat,
  getChat,
  listChats,
  postChatMessageStream,
  type ChatCitation,
  type ChatMessage,
  type ContextUsage,
  type ConversationSummary,
} from "../../api/chats";
import { Crumbs } from "../../components/Crumbs";
import { claimHaptic, triggerHaptic } from "../../lib/haptics";
import { MarkdownContent } from "../../components/MarkdownContent";
import { PushButton } from "../../components/PushButton";

const COMPACT_CHAT_QUERY = "(max-width: 860px)";

const POLICY_STAGE_PHRASE: Record<string, string> = {
  checking: "Checking your question",
  searching: "Searching policy documents",
  summarising: "Writing the answer",
};

function prefersCompactChats(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia(COMPACT_CHAT_QUERY).matches;
}

function useCompactChats(): boolean {
  const [compact, setCompact] = useState(prefersCompactChats);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(COMPACT_CHAT_QUERY);
    const onChange = () => setCompact(query.matches);
    onChange();
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  return compact;
}

function scrollThreadToEnd(node: HTMLElement | null) {
  if (!node) return;
  try {
    node.scrollTo({ top: node.scrollHeight });
  } catch {
    node.scrollTop = node.scrollHeight;
  }
}

function emptyContext(): ContextUsage {
  return { used_tokens: 0, limit_tokens: 20_000, used_percent: 0 };
}

function formatTokenCount(value: number): string {
  return Math.max(0, Math.round(value))
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

export function citationSourceType(label: string): string {
  const text = label.toLowerCase();
  if (text.includes("handbook")) return "Handbook";
  if (text.includes("memo")) return "Memo";
  if (text.includes("curriculum") || text.includes("lesson")) return "Curriculum";
  if (text.includes("integrity") || text.includes("guide")) return "Guide";
  if (text.includes("policy")) return "Policy";
  return "Document";
}

function CitationSources({ citations }: { citations: ChatCitation[] }) {
  const types = [...new Set(citations.map((cite) => citationSourceType(cite.label)))].sort();
  const [selected, setSelected] = useState("all");
  const visible =
    selected === "all"
      ? citations
      : citations.filter((cite) => citationSourceType(cite.label) === selected);

  return (
    <details className="policy-chat__sources">
      <summary className="policy-chat__sources-toggle">
        Sources ({citations.length})
      </summary>
      <label className="policy-chat__sources-label">
        Filter
        <select
          className="form__input policy-chat__sources-select"
          value={selected}
          onChange={(event) => setSelected(event.target.value)}
        >
          <option value="all">All types</option>
          {types.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
      </label>
      <ul className="policy-chat__citations">
        {visible.map((cite) => (
          <li key={cite.id}>
            <span className="policy-chat__cite-type">{citationSourceType(cite.label)}</span>
            <span className="policy-chat__cite-label">{cite.label}</span>
            <span className="policy-chat__cite-excerpt">{cite.excerpt}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}

function ContextMeter({ context }: { context: ContextUsage }) {
  const pct = Math.min(100, Math.max(0, context.used_percent));
  const used = formatTokenCount(context.used_tokens);
  const limit = formatTokenCount(context.limit_tokens);
  const usageLabel = `${used} / ${limit} tokens · ${pct}%`;
  const radius = 14;
  const circumference = 2 * Math.PI * radius;
  const dash = (pct / 100) * circumference;

  return (
    <div
      className="policy-chat__context"
      role="status"
      tabIndex={0}
      aria-label={`Context window ${used} of ${limit} tokens, ${pct} percent`}
    >
      <svg className="policy-chat__context-ring" viewBox="0 0 36 36" aria-hidden="true">
        <circle className="policy-chat__context-track" cx="18" cy="18" r={radius} />
        <circle
          className="policy-chat__context-fill"
          cx="18"
          cy="18"
          r={radius}
          strokeDasharray={`${dash} ${circumference}`}
          transform="rotate(-90 18 18)"
        />
      </svg>
      <span className="policy-chat__context-tip">{usageLabel}</span>
    </div>
  );
}

export function PolicyChatPage() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [context, setContext] = useState<ContextUsage>(emptyContext());
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [think, setThink] = useState<string | null>(null);
  const [draftAnswer, setDraftAnswer] = useState("");
  const [loadingList, setLoadingList] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const compact = useCompactChats();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(prefersCompactChats);
  const threadRef = useRef<HTMLDivElement>(null);
  const wasCompact = useRef(compact);

  const refreshList = useCallback(async () => {
    const rows = await listChats();
    setConversations(rows);
    return rows;
  }, []);

  const loadConversation = useCallback(async (id: string) => {
    const detail = await getChat(id);
    setActiveId(detail.id);
    setMessages(detail.messages);
    setContext(detail.context);
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoadingList(true);
      setError(null);
      try {
        let rows = await refreshList();
        if (cancelled) return;
        if (rows.length === 0) {
          const created = await createChat();
          if (cancelled) return;
          rows = await refreshList();
          await loadConversation(created.id);
        } else {
          await loadConversation(rows[0].id);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load chats");
        }
      } finally {
        if (!cancelled) setLoadingList(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadConversation, refreshList]);

  useEffect(() => {
    scrollThreadToEnd(threadRef.current);
  }, [messages, busy, think, draftAnswer]);

  useEffect(() => {
    if (compact && !wasCompact.current) setSidebarCollapsed(true);
    wasCompact.current = compact;
  }, [compact]);

  async function onNewChat() {
    setBusy(true);
    setError(null);
    try {
      const created = await createChat();
      await refreshList();
      await loadConversation(created.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create chat");
    } finally {
      setBusy(false);
    }
  }

  async function onSelectChat(id: string) {
    if (id === activeId || busy) return;
    setBusy(true);
    setError(null);
    try {
      await loadConversation(id);
      if (compact) setSidebarCollapsed(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not open chat");
    } finally {
      setBusy(false);
    }
  }

  async function onDeleteChat(id: string) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await deleteChat(id);
      const rows = await refreshList();
      if (rows.length === 0) {
        const created = await createChat();
        await refreshList();
        await loadConversation(created.id);
      } else {
        const next = rows.find((r) => r.id !== id) ?? rows[0];
        await loadConversation(next.id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete chat");
    } finally {
      setBusy(false);
    }
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy || !activeId) return;
    claimHaptic();

    setDraft("");
    setBusy(true);
    setError(null);
    setThink("Checking your question");
    setDraftAnswer("");
    const optimistic: ChatMessage = {
      id: `local-${Date.now()}`,
      role: "user",
      content: text,
      created_at: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, optimistic]);

    try {
      const result = await postChatMessageStream(activeId, text, {
        onProgress: (progress) => {
          if (progress.status !== "running") return;
          const phrase = progress.message ?? POLICY_STAGE_PHRASE[progress.stage];
          if (phrase) setThink(phrase);
        },
        onAnswerDelta: (delta) => setDraftAnswer((prev) => prev + delta),
        onAnswerReset: () => setDraftAnswer(""),
      });
      if (result.kind === "error") {
        triggerHaptic("error");
        setMessages((prev) => prev.filter((m) => m.id !== optimistic.id));
        setError(result.message);
        return;
      }
      const saved = result.kind === "final" ? result.message : result.saved;
      if (!saved) {
        triggerHaptic("error");
        setMessages((prev) => prev.filter((m) => m.id !== optimistic.id));
        setError(result.message);
        return;
      }
      setMessages((prev) => {
        const withoutOptimistic = prev.filter((m) => m.id !== optimistic.id);
        return [...withoutOptimistic, saved.user_message, saved.assistant_message];
      });
      setContext(saved.context);
      await refreshList();
      triggerHaptic("success");
    } catch (err) {
      triggerHaptic("error");
      setMessages((prev) => prev.filter((m) => m.id !== optimistic.id));
      setError(err instanceof Error ? err.message : "Send failed");
    } finally {
      setBusy(false);
      setThink(null);
      setDraftAnswer("");
    }
  }

  return (
    <div className="policy-chat">
      <h1 className="sr-only">Policy assistant</h1>
      <Crumbs parts={[{ label: "Policy assistant" }]} />

      {error && (
        <p className="banner banner--warn" role="alert">
          {error}
        </p>
      )}

      <div className={`policy-chat__layout${sidebarCollapsed ? " is-sidebar-collapsed" : ""}`}>
        {compact && !sidebarCollapsed ? (
          <button
            type="button"
            className="policy-chat__sheet-backdrop"
            aria-label="Close chats"
            onClick={() => setSidebarCollapsed(true)}
          />
        ) : null}
        <aside className="policy-chat__sidebar" aria-label="Conversations">
          <div className="policy-chat__sidebar-head">
            <h2>Chats</h2>
            <div className="policy-chat__sidebar-actions">
              <PushButton
                type="button"
                size="sm"
                className="policy-chat__sidebar-new"
                onClick={() => void onNewChat()}
                disabled={busy || (sidebarCollapsed && !compact)}
                aria-hidden={sidebarCollapsed && !compact ? true : undefined}
                tabIndex={sidebarCollapsed && !compact ? -1 : undefined}
              >
                New
              </PushButton>
              <button
                type="button"
                className="policy-chat__sidebar-toggle"
                aria-expanded={!sidebarCollapsed}
                aria-controls="policy-chat-conversations"
                title={sidebarCollapsed ? "Expand chats" : "Collapse chats"}
                aria-label={sidebarCollapsed ? "Expand chats" : "Collapse chats"}
                onClick={() => setSidebarCollapsed((collapsed) => !collapsed)}
              >
                {sidebarCollapsed ? "»" : "«"}
              </button>
            </div>
          </div>
          <div
            id="policy-chat-conversations"
            className="policy-chat__sidebar-body"
            hidden={sidebarCollapsed}
          >
            {loadingList ? (
              <p className="muted">Loading…</p>
            ) : (
              <ul className="policy-chat__conv-list">
                {conversations.map((conv) => (
                  <li key={conv.id}>
                    <button
                      type="button"
                      className={
                        conv.id === activeId
                          ? "policy-chat__conv policy-chat__conv--active"
                          : "policy-chat__conv"
                      }
                      onClick={() => void onSelectChat(conv.id)}
                      disabled={busy}
                    >
                      <span className="policy-chat__conv-title">{conv.title}</span>
                      <span className="policy-chat__conv-meta">{conv.context.used_percent}% ctx</span>
                    </button>
                    <button
                      type="button"
                      className="policy-chat__conv-delete"
                      aria-label={`Delete ${conv.title}`}
                      onClick={() => void onDeleteChat(conv.id)}
                      disabled={busy}
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </aside>

        <div className="policy-chat__main">
          <div className="policy-chat__thread" ref={threadRef} aria-live="polite">
            {messages.map((msg) => (
              <article
                key={msg.id}
                className={`policy-chat__bubble policy-chat__bubble--${msg.role === "user" ? "user" : "assistant"}`}
                aria-label={msg.role === "user" ? "You" : "Assistant"}
              >
                {msg.role === "assistant" ? (
                  <MarkdownContent className="policy-chat__body markdown">{msg.content}</MarkdownContent>
                ) : (
                  <p className="policy-chat__body">{msg.content}</p>
                )}
                {msg.citations && msg.citations.length > 0 && (
                  <CitationSources citations={msg.citations} />
                )}
              </article>
            ))}
            {busy && think ? (
              <p className="ask-think" role="status">
                <span className="ask-think__orb" aria-hidden="true" />
                <span className="ask-think__phrase" key={think}>
                  {think}
                </span>
              </p>
            ) : null}
            {busy && draftAnswer ? (
              <article
                className="policy-chat__bubble policy-chat__bubble--assistant"
                aria-label="Assistant"
              >
                <MarkdownContent className="policy-chat__body markdown">{draftAnswer}</MarkdownContent>
              </article>
            ) : null}
          </div>

          <form className="policy-chat__composer" onSubmit={(e) => void onSubmit(e)}>
            <label className="sr-only" htmlFor="policy-chat-input">
              Message
            </label>
            <textarea
              id="policy-chat-input"
              className="policy-chat__input"
              rows={2}
              placeholder="e.g. What is the attendance escalation policy?"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              disabled={busy || !activeId}
            />
            <ContextMeter context={context} />
            <PushButton
              type="submit"
              disabled={busy || !draft.trim() || !activeId}
              loading={busy}
              onClick={() => {
                if (!busy && draft.trim() && activeId) claimHaptic();
              }}
            >
              Send
            </PushButton>
          </form>
        </div>
      </div>
    </div>
  );
}
