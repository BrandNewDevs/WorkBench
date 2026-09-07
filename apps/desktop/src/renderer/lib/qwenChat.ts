import type { ChatMessage } from "../../shared/contracts";
import { LocalApiError } from "../api/localApi.ts";

export interface QwenConversationStatus {
  selectedModel: string;
  usedFallback: boolean;
}

/**
 * Small, non-sensitive status text required by issue #56: the selected model
 * ID, the fact that it is a local model, and a fallback note only when a
 * fallback actually occurred.
 */
export function statusLine(status: QwenConversationStatus): string {
  const fallback = status.usedFallback ? " · fallback model used" : "";
  return `${status.selectedModel} · local model${fallback}`;
}

/** Rejected before any request when a fresh draft exceeds the wire limit. */
export const oversizedMessageText =
  "The message is too long for the local service. Shorten it and try again.";

/**
 * Plain, actionable error text for conversation failures. Backend bodies,
 * prompts, and stack traces are never surfaced to the user.
 */
export function conversationFailureMessage(error: unknown): string {
  if (error instanceof LocalApiError) {
    if (error.kind === "unauthorized") {
      return "Your local employee session could not be verified. Sign in again.";
    }
    if (error.kind === "timeout") {
      return "The local text generation timed out before completing. Try again.";
    }
    if (error.kind === "network") {
      return "The local service is unavailable. Confirm FastAPI is running and try again.";
    }
    if (error.kind === "resourceNotFound") {
      return "This conversation no longer exists. Start a new conversation.";
    }
    if (error.kind === "endpointUnavailable") {
      return "The local service does not provide local chat yet. Update the local service and try again.";
    }
    if (error.kind === "malformedJson" || error.kind === "invalidResponse") {
      return "The local service returned an unreadable response. Try again.";
    }
    if (error.kind === "http") {
      switch (error.errorCode) {
        case "text_model_unavailable":
          return "The local text model is unavailable. Check the installed model and try again.";
        case "ollama_unavailable":
          return "The local Ollama service is unavailable. Start Ollama and try again.";
        case "generation_timeout":
          return "The local text generation timed out before completing. Try again.";
        case "conversation_too_large":
          return "This conversation is too long for the local model. Start a new conversation.";
        case "invalid_ai_response":
          return "The local model returned an unusable response. Try again.";
        case "conversation_conflict":
          return "Another operation is updating this conversation. Try again in a moment.";
        case "session_not_active":
          return "This conversation is closed. Start a new conversation to continue chatting.";
        case "chat_store_unavailable":
          return "Local conversation storage is unavailable. Try again when the local service is healthy.";
        case "invalid_message":
          return "The message could not be sent as written. Edit it and try again.";
        default:
          return `The local service refused the request (HTTP ${error.status ?? "unknown"}). Try again.`;
      }
    }
  }
  return "The message could not be sent.";
}

/** Renderer-side stand-in for a user message before FastAPI confirms the turn. */
export function pendingUserMessage(clientRequestId: string, content: string, now: number): ChatMessage {
  return {
    messageId: clientRequestId,
    sessionId: clientRequestId,
    authorUserId: null,
    role: "user",
    content,
    createdAt: new Date(now).toISOString(),
    clientMessageId: clientRequestId,
  };
}

/** The optimistic-concurrency key of a delivered conversation turn. */
export function turnWasStored(messages: readonly ChatMessage[], clientRequestId: string): boolean {
  return messages.some((message) => message.role === "user" && message.clientMessageId === clientRequestId);
}
