import assert from "node:assert/strict";
import { test } from "node:test";
import type { ChatMessage, ChatSession } from "../src/shared/contracts.ts";
import { conversationMessageMaxLength } from "../src/shared/contracts.ts";
import { LocalApiError } from "../src/renderer/api/localApi.ts";
import {
  conversationFailureMessage,
  offlineChatStatusText,
  oversizedMessageText,
  pendingUserMessage,
  statusLine,
  turnWasStored,
} from "../src/renderer/lib/qwenChat.ts";
import { defaultEmployeeWorkspaceView } from "../src/renderer/lib/workspace.ts";
import {
  initialQwenChatState,
  qwenChatReducer,
  type QwenChatState,
} from "../src/renderer/hooks/useQwenChat.ts";

const clientRequestId = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d";
const clientSessionId = "2a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d";

function storedMessage(
  clientMessageId: string | null,
  role: "user" | "assistant" = "user",
  content = "Stored content",
): ChatMessage {
  return {
    messageId: "3a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
    sessionId: "4a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
    authorUserId: null,
    role,
    content,
    createdAt: "2026-09-07T09:00:00Z",
    clientMessageId,
  };
}

function qwenState(overrides: Partial<QwenChatState> = {}): QwenChatState {
  return { ...initialQwenChatState, ...overrides };
}

function pickerSession(
  status: ChatSession["status"],
  title = "Local Qwen chat",
  sessionId = "5a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
  workflowType: ChatSession["workflowType"] = "localConversation",
): ChatSession {
  return {
    sessionId,
    ownerUserId: "6a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
    workflowType,
    title,
    stage: "collectingInputs",
    status,
    createdAt: "2026-09-07T09:00:00Z",
    updatedAt: "2026-09-07T09:05:00Z",
    clientSessionId: null,
  };
}

test("status labels name the selected local model and only real fallbacks", () => {
  assert.equal(statusLine({ selectedModel: "qwen3:4b", usedFallback: false }), "qwen3:4b · local model");
  assert.equal(
    statusLine({ selectedModel: "qwen3:1.7b", usedFallback: true }),
    "qwen3:1.7b · local model · fallback model used",
  );
});

test("ordinary local chat is the default employee workspace with a permanent offline notice", () => {
  assert.equal(defaultEmployeeWorkspaceView, "qwenChat");
  assert.equal(offlineChatStatusText, "Offline — no live web verification");
});

test("conversation failures map to plain, actionable, non-sensitive text", () => {
  const expected: ReadonlyArray<[LocalApiError, string]> = [
    [new LocalApiError("internal", "unauthorized", 401), "Your local employee session could not be verified. Sign in again."],
    [new LocalApiError("internal", "timeout"), "The local text generation timed out before completing. Try again."],
    [new LocalApiError("internal", "network"), "The local service is unavailable. Confirm FastAPI is running and try again."],
    [new LocalApiError("internal", "resourceNotFound", 404), "This conversation no longer exists. Start a new conversation."],
    [
      new LocalApiError("internal", "http", 503, "text_model_unavailable"),
      "The local text model is unavailable. Check the installed model and try again.",
    ],
    [
      new LocalApiError("internal", "http", 503, "ollama_unavailable"),
      "The local Ollama service is unavailable. Start Ollama and try again.",
    ],
    [
      new LocalApiError("internal", "http", 504, "generation_timeout"),
      "The local text generation timed out before completing. Try again.",
    ],
    [
      new LocalApiError("internal", "http", 413, "conversation_too_large"),
      "This conversation is too long for the local model. Start a new conversation.",
    ],
    [
      new LocalApiError("internal", "http", 502, "invalid_ai_response"),
      "The local model returned an unusable response. Try again.",
    ],
    [
      new LocalApiError("internal", "http", 409, "conversation_conflict"),
      "Another operation is updating this conversation. Try again in a moment.",
    ],
    [
      new LocalApiError("internal", "http", 409, "session_not_active"),
      "This conversation is closed. Start a new conversation to continue chatting.",
    ],
    [
      new LocalApiError("internal", "http", 503, "chat_store_unavailable"),
      "Local conversation storage is unavailable. Try again when the local service is healthy.",
    ],
  ];
  for (const [error, expectedMessage] of expected) {
    assert.equal(conversationFailureMessage(error), expectedMessage);
  }

  // Unknown codes stay generic: backend detail must never reach the user.
  const generic = conversationFailureMessage(new LocalApiError("secret internal detail", "http", 500, "mystery_code"));
  assert.equal(generic, "The local service refused the request (HTTP 500). Try again.");
  assert.ok(!generic.includes("secret internal detail"));
  assert.equal(conversationFailureMessage(new Error("raw failure")), "The message could not be sent.");
});

test("pending user messages carry the idempotency key for reconciliation", () => {
  const now = Date.parse("2026-09-07T09:30:00Z");
  const pending = pendingUserMessage(clientRequestId, "What is the seal clearance?", now);
  assert.equal(pending.messageId, clientRequestId);
  assert.equal(pending.clientMessageId, clientRequestId);
  assert.equal(pending.role, "user");
  assert.equal(pending.content, "What is the seal clearance?");
  assert.match(pending.createdAt, /^2026-09-07T/);
});

test("turn delivery is detected through the stored idempotency key", () => {
  const stored = [storedMessage(clientRequestId), storedMessage(null, "assistant")];
  assert.equal(turnWasStored(stored, clientRequestId), true);
  assert.equal(turnWasStored([storedMessage("9a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d")], clientRequestId), false);
  assert.equal(turnWasStored([], clientRequestId), false);
});

test("oversized fresh drafts are rejected with plain text before any request", () => {
  assert.equal(oversizedMessageText, "The message is too long for the local service. Shorten it and try again.");
  assert.equal(conversationMessageMaxLength, 20_000);
});

test("a retried turn never renders a duplicate optimistic message", () => {
  const optimistic = pendingUserMessage(clientRequestId, "Question", Date.now());
  const once = qwenChatReducer(qwenState(), { type: "optimisticUserMessage", message: optimistic });
  const twice = qwenChatReducer(once, { type: "optimisticUserMessage", message: optimistic });
  assert.equal(twice, once);
  assert.equal(twice.messages.length, 1);
});

test("the picker offers only active plain-chat sessions", () => {
  const qwenId = "5a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d";
  const closedQwenId = "8a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d";
  const result = qwenChatReducer(qwenState({ pickerState: "loading" }), {
    type: "pickerLoaded",
    sessions: [
      pickerSession("active", "Pump seal question", qwenId),
      pickerSession("active", "Inspection workflow review", "7a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d", "inspectionAnalysis"),
      pickerSession("active", "Code repair review", "9a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d", "codeRepair"),
      pickerSession("completed", "Closed qwen chat", closedQwenId),
      pickerSession("failed", "Failed qwen chat", "aa2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"),
    ],
  });
  assert.equal(result.pickerState, "ready");
  // The data-layer type separates plain chat from workflows: neither the
  // workflow workspace nor this picker can cross that boundary.
  assert.deepEqual(result.pickerSessions, [{ sessionId: qwenId, title: "Pump seal question" }]);
});

test("a created conversation joins the picker exactly once so it can be resumed", () => {
  const bound = qwenState({
    creatingSession: true,
    pickerSessions: [{ sessionId: "5a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d", title: "Earlier conversation" }],
  });
  const created = qwenChatReducer(bound, {
    type: "sessionCreated",
    session: pickerSession("active", "Pump seal question", "7a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"),
  });
  assert.equal(created.creatingSession, false);
  assert.equal(created.sessionId, "7a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d");
  assert.deepEqual(created.pickerSessions, [
    { sessionId: "5a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d", title: "Earlier conversation" },
    { sessionId: "7a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d", title: "Pump seal question" },
  ]);

  const replayed = qwenChatReducer(created, {
    type: "sessionCreated",
    session: pickerSession("active", "Pump seal question", "7a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"),
  });
  assert.equal(replayed.pickerSessions.length, 2);
});

test("a definitive send failure removes the optimistic message and clears pending keys", () => {
  const started = qwenChatReducer(qwenState({ draft: "Question", sendError: "older error" }), {
    type: "sendStarted",
    clientRequestId,
    clientSessionId,
    submittedDraft: "Question",
  });
  const optimistic = qwenChatReducer(started, {
    type: "optimisticUserMessage",
    message: pendingUserMessage(clientRequestId, "Question", Date.now()),
  });
  const failed = qwenChatReducer(optimistic, {
    type: "sendFailed",
    message: "The local text model is unavailable. Check the installed model and try again.",
    definitive: true,
  });

  assert.equal(failed.sendState, "error");
  assert.deepEqual(failed.messages, []);
  assert.equal(failed.pendingClientRequestId, undefined);
  assert.equal(failed.pendingClientSessionId, undefined);
  assert.equal(failed.pendingDraft, undefined);
  assert.equal(failed.draft, "Question");
});

test("an ambiguous send failure keeps the pending keys, snapshot, and optimistic message", () => {
  const started = qwenChatReducer(qwenState({ draft: "Question" }), {
    type: "sendStarted",
    clientRequestId,
    clientSessionId,
    submittedDraft: "Question",
  });
  const optimistic = qwenChatReducer(started, {
    type: "optimisticUserMessage",
    message: pendingUserMessage(clientRequestId, "Question", Date.now()),
  });
  const failed = qwenChatReducer(optimistic, {
    type: "sendFailed",
    message: "The local service is unavailable. Confirm FastAPI is running and try again. Delivery is unconfirmed; sending again reuses the same request.",
    definitive: false,
  });

  assert.equal(failed.sendState, "error");
  assert.equal(failed.messages.length, 1);
  assert.equal(failed.pendingClientRequestId, clientRequestId);
  assert.equal(failed.pendingClientSessionId, clientSessionId);
  assert.equal(failed.pendingDraft, "Question");
  assert.equal(failed.draft, "Question");
});

test("a retry of an ambiguous send reuses the same keys and snapshot", () => {
  const started = qwenChatReducer(qwenState({ draft: "Question" }), {
    type: "sendStarted",
    clientRequestId,
    clientSessionId,
    submittedDraft: "Question",
  });
  const editedThenRetried = qwenChatReducer(started, {
    type: "sendStarted",
    clientRequestId: "8a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
    clientSessionId: "8a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5e",
    submittedDraft: "Edited draft",
  });
  assert.equal(editedThenRetried.pendingClientRequestId, clientRequestId);
  assert.equal(editedThenRetried.pendingDraft, "Question");
  assert.equal(editedThenRetried.pendingClientSessionId, clientSessionId);
});

test("retry preserves a newer unsent draft", () => {
  const pending = qwenChatReducer(qwenState({ draft: "Question" }), {
    type: "sendStarted", clientRequestId, clientSessionId, submittedDraft: "Question",
  });
  const failed = qwenChatReducer(pending, { type: "sendFailed", definitive: false, message: "Timeout" });
  const retry = qwenChatReducer({ ...failed, draft: "My revised question" }, {
    type: "sendStarted", clientRequestId, clientSessionId, submittedDraft: "Question",
  });
  assert.equal(retry.draft, "My revised question");
  assert.equal(retry.pendingDraft, "Question");
});

test("a completed turn adopts the stored conversation, status, and clears the matching draft", () => {
  const sending = qwenChatReducer(
    qwenState({ draft: "Question", sessionId: "5a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d", creatingSession: true }),
    { type: "sendStarted", clientRequestId, clientSessionId, submittedDraft: "Question" },
  );
  const completed = qwenChatReducer(
    qwenChatReducer(sending, { type: "optimisticUserMessage", message: pendingUserMessage(clientRequestId, "Question", Date.now()) }),
    {
      type: "turnCompleted",
      messages: [storedMessage(clientRequestId), storedMessage(null, "assistant", "Local Qwen reply.")],
      status: { selectedModel: "qwen3:4b", usedFallback: false },
      submittedDraft: "Question",
    },
  );

  assert.equal(completed.sendState, "idle");
  assert.equal(completed.sendError, undefined);
  assert.equal(completed.draft, "");
  assert.equal(completed.pendingClientRequestId, undefined);
  assert.equal(completed.creatingSession, false);
  assert.deepEqual(completed.status, { selectedModel: "qwen3:4b", usedFallback: false });
  assert.deepEqual(completed.messages, [
    storedMessage(clientRequestId),
    storedMessage(null, "assistant", "Local Qwen reply."),
  ]);
});

test("a draft edited during generation survives the completed turn", () => {
  const sending = qwenChatReducer(qwenState({ draft: "Question" }), {
    type: "sendStarted",
    clientRequestId,
    clientSessionId,
    submittedDraft: "Question",
  });
  const edited = { ...sending, draft: "Question, but revised" };
  const completed = qwenChatReducer(edited, {
    type: "turnCompleted",
    messages: [storedMessage(clientRequestId)],
    status: { selectedModel: "qwen3:4b", usedFallback: false },
    submittedDraft: "Question",
  });
  assert.equal(completed.draft, "Question, but revised");
});

test("selecting a conversation resets state and starting a new one clears the bound session", () => {
  const bound: QwenChatState = qwenState({
    sessionId: "5a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
    sessionTitle: "Pump seal question",
    messages: [storedMessage(clientRequestId)],
    messagesState: "ready",
    status: { selectedModel: "qwen3:4b", usedFallback: false },
    draft: "Leftover draft",
  });

  const selected = qwenChatReducer(bound, {
    type: "sessionSelected",
    sessionId: "7a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
    title: "Other conversation",
  });
  assert.equal(selected.sessionId, "7a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d");
  assert.equal(selected.sessionTitle, "Other conversation");
  assert.deepEqual(selected.messages, []);
  assert.equal(selected.messagesState, "loading");
  assert.equal(selected.status, undefined);
  assert.equal(selected.draft, "");

  const fresh = qwenChatReducer(bound, { type: "newConversation" });
  assert.equal(fresh.sessionId, undefined);
  assert.deepEqual(fresh.messages, []);
  assert.equal(fresh.messagesState, "idle");
  assert.equal(fresh.draft, "");
});
