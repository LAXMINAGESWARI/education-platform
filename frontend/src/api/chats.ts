import { apiRequest, apiStream } from "./client";
import { parseProgressEnvelope, type ProgressEnvelope } from "./progressEnvelope";
import { subscribeSse, type ProgressReconnectNotice, type SseFrame } from "./sseReconnect";
import { noteProgressSeq, resetProgressCursor } from "./stageOrder";

export type ChatCitation = {
  id: string;
  label: string;
  excerpt: string;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  citations?: ChatCitation[] | null;
  created_at: string;
};

export type ContextUsage = {
  used_tokens: number;
  limit_tokens: number;
  used_percent: number;
};

export type ConversationSummary = {
  id: string;
  title: string;
  updated_at: string;
  context: ContextUsage;
};

export type ConversationDetail = ConversationSummary & {
  messages: ChatMessage[];
};

export type PostMessageResult = {
  user_message: ChatMessage;
  assistant_message: ChatMessage;
  context: ContextUsage;
};

export function listChats(): Promise<ConversationSummary[]> {
  return apiRequest<ConversationSummary[]>("/chats", { auth: true });
}

export function createChat(title?: string): Promise<ConversationSummary> {
  return apiRequest<ConversationSummary>("/chats", {
    method: "POST",
    auth: true,
    body: title ? { title } : {},
  });
}

export function getChat(conversationId: string): Promise<ConversationDetail> {
  return apiRequest<ConversationDetail>(`/chats/${encodeURIComponent(conversationId)}`, {
    auth: true,
  });
}

export function deleteChat(conversationId: string): Promise<void> {
  return apiRequest<void>(`/chats/${encodeURIComponent(conversationId)}`, {
    method: "DELETE",
    auth: true,
  });
}

export function postChatMessage(
  conversationId: string,
  content: string,
): Promise<PostMessageResult> {
  return apiRequest<PostMessageResult>(
    `/chats/${encodeURIComponent(conversationId)}/messages`,
    {
      method: "POST",
      auth: true,
      body: { content },
    },
  );
}

export type PolicyStreamHandlers = {
  requestId?: string;
  signal?: AbortSignal;
  onProgress?: (progress: ProgressEnvelope) => void;
  onAnswerDelta?: (text: string) => void;
  onAnswerReset?: () => void;
  onReconnecting?: (notice: ProgressReconnectNotice) => void;
};

export type PolicyStreamResult =
  | { kind: "final"; requestId: string; message: PostMessageResult }
  | { kind: "refusal"; requestId: string; message: string; saved?: PostMessageResult }
  | { kind: "error"; requestId: string; message: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isChatMessage(value: unknown): value is ChatMessage {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === "string" &&
    (value.role === "user" ||
      value.role === "assistant" ||
      value.role === "system" ||
      value.role === "tool") &&
    typeof value.content === "string" &&
    typeof value.created_at === "string"
  );
}

function isContext(value: unknown): value is ContextUsage {
  return (
    isRecord(value) &&
    typeof value.used_tokens === "number" &&
    typeof value.limit_tokens === "number" &&
    typeof value.used_percent === "number"
  );
}

function parseSaved(payload: Record<string, unknown>): PostMessageResult | null {
  if (
    !isChatMessage(payload.user_message) ||
    !isChatMessage(payload.assistant_message) ||
    !isContext(payload.context)
  ) {
    return null;
  }
  return {
    user_message: payload.user_message,
    assistant_message: payload.assistant_message,
    context: payload.context,
  };
}

function parseJsonObject(data: string, label: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(data) as unknown;
  } catch {
    throw new SyntaxError(`${label} was not JSON`);
  }
  if (!isRecord(parsed)) {
    throw new SyntaxError(`${label} was not an object`);
  }
  return parsed;
}

function parsePolicyFrame(
  frame: SseFrame,
  requestId: string,
): { stop: PolicyStreamResult } | { progress: ProgressEnvelope } | { delta: string } | { ignore: true } {
  switch (frame.event) {
    case "heartbeat":
    case "table":
      return { ignore: true };
    case "progress": {
      const progress = parseProgressEnvelope(frame.data);
      if (progress.kind !== "policy_chat") {
        throw new SyntaxError("Policy stream progress kind was not policy_chat");
      }
      return { progress };
    }
    case "answer_delta": {
      const payload = parseJsonObject(frame.data, "Answer delta");
      if (typeof payload.text !== "string") {
        throw new SyntaxError("Answer delta had no text");
      }
      return { delta: payload.text };
    }
    case "final": {
      const payload = parseJsonObject(frame.data, "Final");
      if (typeof payload.content !== "string" || !Array.isArray(payload.citations)) {
        throw new SyntaxError("Policy stream final was missing content");
      }
      const saved = parseSaved(payload);
      if (saved === null) {
        throw new SyntaxError("Policy stream final had no saved messages");
      }
      return { stop: { kind: "final", requestId, message: saved } };
    }
    case "refusal": {
      const payload = parseJsonObject(frame.data, "Refusal");
      if (typeof payload.message !== "string") {
        throw new SyntaxError("Refusal had no message");
      }
      const saved = parseSaved(payload);
      return {
        stop: {
          kind: "refusal",
          requestId,
          message: payload.message,
          saved: saved ?? undefined,
        },
      };
    }
    case "error": {
      const payload = parseJsonObject(frame.data, "Error");
      if (typeof payload.message !== "string") {
        throw new SyntaxError("Error had no message");
      }
      return { stop: { kind: "error", requestId, message: payload.message } };
    }
    default:
      return { ignore: true };
  }
}

function newRequestId(): string {
  return crypto.randomUUID();
}

/**
 * Stream one policy question. A dropped connection re-POSTs the same
 * `request_id`. The server replays the saved turn instead of inserting again.
 */
export async function postChatMessageStream(
  conversationId: string,
  content: string,
  handlers: PolicyStreamHandlers = {},
): Promise<PolicyStreamResult> {
  const requestId = handlers.requestId ?? newRequestId();
  let outcome: PolicyStreamResult | undefined;
  let cursor = resetProgressCursor();

  await subscribeSse({
    signal: handlers.signal,
    open: (signal) =>
      apiStream(`/chats/${encodeURIComponent(conversationId)}/messages/stream`, {
        method: "POST",
        body: { content, request_id: requestId },
        signal,
      }),
    onReconnecting: (notice) => {
      cursor = resetProgressCursor();
      handlers.onAnswerReset?.();
      handlers.onReconnecting?.(notice);
    },
    onFrame: (frame) => {
      const parsed = parsePolicyFrame(frame, requestId);
      if ("ignore" in parsed) return;
      if ("delta" in parsed) {
        handlers.onAnswerDelta?.(parsed.delta);
        return;
      }
      if ("progress" in parsed) {
        const noted = noteProgressSeq(cursor, parsed.progress.seq);
        cursor = noted.cursor;
        if (!noted.accept) return;
        handlers.onProgress?.(parsed.progress);
        return;
      }
      outcome = parsed.stop;
      return "stop";
    },
  });

  if (outcome === undefined) {
    throw new SyntaxError("Policy stream ended without a final frame");
  }
  return outcome;
}
