import { useCallback, useEffect, useReducer, useRef } from "react";
import { conversationMessageMaxLength } from "../../shared/contracts.ts";
import type { ChatMessage, ChatSession } from "../../shared/contracts";
import { LocalApiError, apiFailureWasDefinitive, localApi } from "../api/localApi.ts";
import { chatSessionTitleFromDraft } from "../lib/chatThreads.ts";
import {
  conversationFailureMessage,
  oversizedMessageText,
  pendingUserMessage,
  turnWasStored,
  type QwenConversationStatus,
} from "../lib/qwenChat.ts";

const unconfirmedDeliverySuffix = " Delivery is unconfirmed; sending again reuses the same request.";

export type QwenPickerState = "idle" | "loading" | "ready" | "error";
export type QwenMessagesState = "idle" | "loading" | "ready" | "error";
export type QwenSendState = "idle" | "sending" | "error";

export interface QwenPickerSession {
  sessionId: string;
  title: string;
}

export interface QwenChatState {
  /** Bound local session; absent until the first send is accepted. */
  sessionId?: string;
  sessionTitle?: string;
  draft: string;
  pickerState: QwenPickerState;
  pickerSessions: readonly QwenPickerSession[];
  messages: readonly ChatMessage[];
  messagesState: QwenMessagesState;
  sendState: QwenSendState;
  creatingSession: boolean;
  sendError?: string;
  status?: QwenConversationStatus;
  /** Client idempotency key of an unresolved turn; retries reuse it. */
  pendingClientRequestId?: string;
  /** Draft snapshot bound to the pending keys; retries resend it. */
  pendingDraft?: string;
  /** Client idempotency key of an unresolved session create. */
  pendingClientSessionId?: string;
}

export const initialQwenChatState: QwenChatState = {
  draft: "",
  pickerState: "idle",
  pickerSessions: [],
  messages: [],
  messagesState: "idle",
  sendState: "idle",
  creatingSession: false,
};

type QwenChatAction =
  | { type: "draftChanged"; draft: string }
  | { type: "pickerLoading" }
  | { type: "pickerLoaded"; sessions: readonly ChatSession[] }
  | { type: "pickerFailed" }
  | { type: "sessionSelected"; sessionId: string; title: string }
  | { type: "newConversation" }
  | { type: "messagesLoading" }
  | { type: "messagesLoaded"; messages: readonly ChatMessage[] }
  | { type: "messagesFailed" }
  | { type: "sendStarted"; clientRequestId: string; clientSessionId: string; submittedDraft: string }
  | { type: "optimisticUserMessage"; message: ChatMessage }
  | { type: "sessionCreating" }
  | { type: "sessionCreated"; session: ChatSession }
  | { type: "turnCompleted"; messages: readonly ChatMessage[]; status: QwenConversationStatus; submittedDraft: string }
  | { type: "turnRecovered"; messages: readonly ChatMessage[]; submittedDraft: string }
  | { type: "sendFailed"; message: string; definitive: boolean };

export function qwenChatReducer(state: QwenChatState, action: QwenChatAction): QwenChatState {
  switch (action.type) {
    case "draftChanged":
      return { ...state, draft: action.draft };
    case "pickerLoading":
      return state.pickerState === "loading" ? state : { ...state, pickerState: "loading" };
    case "pickerLoaded": {
      // Only active plain-chat sessions are eligible conversation targets.
      // Their data-layer type keeps workflow sessions out of this picker, and
      // the workflow workspace filters these sessions out in turn.
      const pickerSessions = action.sessions
        .filter((session) => session.status === "active" && session.workflowType === "localConversation")
        .map((session) => ({ sessionId: session.sessionId, title: session.title }));
      return { ...state, pickerState: "ready", pickerSessions };
    }
    case "pickerFailed":
      return { ...state, pickerState: "error" };
    case "sessionSelected":
      return {
        ...state,
        sessionId: action.sessionId,
        sessionTitle: action.title,
        messages: [],
        messagesState: "loading",
        sendState: "idle",
        sendError: undefined,
        status: undefined,
        draft: "",
        pendingClientRequestId: undefined,
        pendingDraft: undefined,
        pendingClientSessionId: undefined,
      };
    case "newConversation":
      return {
        ...state,
        sessionId: undefined,
        sessionTitle: undefined,
        messages: [],
        messagesState: "idle",
        sendState: "idle",
        sendError: undefined,
        status: undefined,
        draft: "",
        pendingClientRequestId: undefined,
        pendingDraft: undefined,
        pendingClientSessionId: undefined,
      };
    case "messagesLoading":
      return state.messagesState === "loading" ? state : { ...state, messagesState: "loading" };
    case "messagesLoaded":
      return { ...state, messages: action.messages, messagesState: "ready" };
    case "messagesFailed":
      return { ...state, messagesState: "error" };
    case "sendStarted":
      return {
        ...state,
        sendState: "sending",
        sendError: undefined,
        // Retrying an older request must preserve a newer unsent draft.
        draft: state.draft === action.submittedDraft ? "" : state.draft,
        // A retry keeps each key bound to the snapshot it was created for;
        // later edits or errant dispatches never rotate the pending identity.
        pendingClientRequestId: state.pendingClientRequestId ?? action.clientRequestId,
        pendingDraft: state.pendingDraft ?? action.submittedDraft,
        pendingClientSessionId: state.pendingClientSessionId ?? action.clientSessionId,
      };
    case "optimisticUserMessage":
      // Idempotent: a retried turn must not render a duplicate bubble.
      return state.messages.some((message) => message.messageId === action.message.messageId)
        ? state
        : { ...state, messages: [...state.messages, action.message] };
    case "sessionCreating":
      return { ...state, creatingSession: true };
    case "sessionCreated": {
      const entry = { sessionId: action.session.sessionId, title: action.session.title };
      const pickerSessions = state.pickerSessions.some((candidate) => candidate.sessionId === entry.sessionId)
        ? state.pickerSessions
        : [...state.pickerSessions, entry];
      return {
        ...state,
        creatingSession: false,
        sessionId: action.session.sessionId,
        sessionTitle: action.session.title,
        pickerSessions,
      };
    }
    case "turnCompleted":
      return {
        ...state,
        messages: action.messages,
        messagesState: "ready",
        sendState: "idle",
        sendError: undefined,
        status: action.status,
        pendingClientRequestId: undefined,
        pendingDraft: undefined,
        pendingClientSessionId: undefined,
        creatingSession: false,
        draft: state.draft === action.submittedDraft ? "" : state.draft,
      };
    case "turnRecovered":
      return {
        ...state,
        messages: action.messages,
        messagesState: "ready",
        sendState: "idle",
        sendError: undefined,
        pendingClientRequestId: undefined,
        pendingDraft: undefined,
        pendingClientSessionId: undefined,
        creatingSession: false,
        draft: state.draft === action.submittedDraft ? "" : state.draft,
      };
    case "sendFailed": {
      // A definitive failure proved nothing was stored, so the optimistic
      // message is removed and the next send starts fresh. An ambiguous
      // failure keeps the pending keys, snapshot, and optimistic message so a
      // retry replays the identical request instead of duplicating the turn.
      const keepPending = !action.definitive;
      const messages = keepPending
        ? state.messages
        : state.messages.filter((message) => message.clientMessageId !== state.pendingClientRequestId);
      return {
        ...state,
        messages,
        sendState: "error",
        sendError: action.message,
        // A locally cleared composer is restored after a failed turn unless
        // the employee has already begun a new draft.
        draft: state.draft.length === 0 ? (state.pendingDraft ?? state.draft) : state.draft,
        pendingClientRequestId: keepPending ? state.pendingClientRequestId : undefined,
        pendingDraft: keepPending ? state.pendingDraft : undefined,
        pendingClientSessionId: keepPending ? state.pendingClientSessionId : undefined,
        creatingSession: false,
      };
    }
  }
}

export interface QwenChat {
  state: QwenChatState;
  setDraft: (draft: string) => void;
  sendMessage: () => void;
  selectSession: (sessionId: string) => void;
  startNewConversation: () => void;
  refreshSessions: () => void;
  retryMessages: () => void;
}

export function useQwenChat({ apiBaseUrl, connected }: { apiBaseUrl: string; connected: boolean }): QwenChat {
  const [state, dispatch] = useReducer(qwenChatReducer, initialQwenChatState);
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  // Reducer state commits a render behind the Send event, so this synchronous
  // gate is authoritative against duplicate sends within one tick.
  const sendInFlightRef = useRef(false);
  const loadSequenceRef = useRef(0);

  const loadMessages = useCallback(
    (sessionId: string) => {
      const sequence = ++loadSequenceRef.current;
      dispatch({ type: "messagesLoading" });
      void localApi.listChatMessages(sessionId, apiBaseUrl).then(
        (response) => {
          if (loadSequenceRef.current === sequence) {
            dispatch({ type: "messagesLoaded", messages: response.messages });
          }
        },
        () => {
          if (loadSequenceRef.current === sequence) {
            dispatch({ type: "messagesFailed" });
          }
        },
      );
    },
    [apiBaseUrl],
  );

  const refreshSessions = useCallback(() => {
    dispatch({ type: "pickerLoading" });
    void localApi.listChatSessions(apiBaseUrl).then(
      (response) => dispatch({ type: "pickerLoaded", sessions: response.sessions }),
      () => dispatch({ type: "pickerFailed" }),
    );
  }, [apiBaseUrl]);

  useEffect(() => {
    if (connected) {
      refreshSessions();
    }
  }, [connected, refreshSessions]);

  const selectSession = useCallback(
    (sessionId: string) => {
      const current = stateRef.current;
      // A switch during an in-flight turn would let its completion overwrite
      // the newly selected conversation, so switching waits for the reply.
      if (current.sendState === "sending" || current.sessionId === sessionId) return;
      const entry = current.pickerSessions.find((candidate) => candidate.sessionId === sessionId);
      if (!entry) return;
      dispatch({ type: "sessionSelected", sessionId, title: entry.title });
      loadMessages(sessionId);
    },
    [loadMessages],
  );

  const startNewConversation = useCallback(() => {
    const current = stateRef.current;
    if (current.sendState === "sending") return;
    if (
      current.sessionId === undefined &&
      current.messages.length === 0 &&
      current.pendingClientRequestId === undefined &&
      current.draft.length === 0
    ) {
      return;
    }
    // Invalidate any in-flight message load: its delayed callback would
    // otherwise install the previous session's messages into the blank
    // conversation this reset creates.
    ++loadSequenceRef.current;
    dispatch({ type: "newConversation" });
  }, []);

  const retryMessages = useCallback(() => {
    const sessionId = stateRef.current.sessionId;
    if (sessionId !== undefined) {
      loadMessages(sessionId);
    }
  }, [loadMessages]);

  const sendMessage = useCallback(() => {
    const current = stateRef.current;
    if (!connected || sendInFlightRef.current || current.sendState === "sending") return;
    const submittedDraft = current.pendingDraft ?? current.draft;
    const content = submittedDraft.trim();
    if (content.length === 0) return;
    // A fresh draft beyond the wire limit is rejected before any dispatch so
    // it can never be retained as an unconfirmed pending request.
    if (content.length > conversationMessageMaxLength) {
      dispatch({ type: "sendFailed", message: oversizedMessageText, definitive: true });
      return;
    }
    const clientRequestId = current.pendingClientRequestId ?? globalThis.crypto.randomUUID();
    const clientSessionId = current.pendingClientSessionId ?? globalThis.crypto.randomUUID();
    sendInFlightRef.current = true;
    dispatch({ type: "sendStarted", clientRequestId, clientSessionId, submittedDraft });
    dispatch({ type: "optimisticUserMessage", message: pendingUserMessage(clientRequestId, content, Date.now()) });
    void (async () => {
      let sessionId = current.sessionId;
      try {
        if (sessionId === undefined) {
          dispatch({ type: "sessionCreating" });
          // The data-layer session type separates plain chat from workflows:
          // the backend refuses workflow admission for it, and the workflow
          // workspace filters these sessions out of its own UI.
          const created = await localApi.createChatSession(
            { workflowType: "localConversation", title: chatSessionTitleFromDraft(content), clientSessionId },
            apiBaseUrl,
          );
          dispatch({ type: "sessionCreated", session: created });
          sessionId = created.sessionId;
        }
        const turn = await localApi.sendConversationMessage(
          sessionId,
          { message: content, clientRequestId },
          apiBaseUrl,
        );
        const stored = await localApi.listChatMessages(sessionId, apiBaseUrl);
        dispatch({
          type: "turnCompleted",
          messages: stored.messages,
          status: { selectedModel: turn.selectedModel, usedFallback: turn.usedFallback },
          submittedDraft,
        });
      } catch (error) {
        const baseMessage = conversationFailureMessage(error);
        // A 409 conversation_conflict can also mean this exact idempotency key
        // was already stored (lost response): the unique index refuses the
        // duplicate append. It reconciles like an ambiguous failure instead of
        // discarding a delivered turn.
        const conflictMayBeStored =
          error instanceof LocalApiError &&
          error.kind === "http" &&
          error.status === 409 &&
          error.errorCode === "conversation_conflict";
        const definitive = apiFailureWasDefinitive(error) && !conflictMayBeStored;
        if (sessionId !== undefined && (conflictMayBeStored || !definitive)) {
          try {
            const stored = await localApi.listChatMessages(sessionId, apiBaseUrl);
            if (turnWasStored(stored.messages, clientRequestId)) {
              dispatch({ type: "turnRecovered", messages: stored.messages, submittedDraft });
              return;
            }
            if (conflictMayBeStored) {
              // Confirmed not stored: the conflict came from another writer.
              dispatch({ type: "sendFailed", message: baseMessage, definitive: true });
              return;
            }
          } catch {
            // Reconciliation failed too; the send error stays visible.
          }
        }
        if (definitive) {
          // FastAPI answered and refused before any write: nothing was stored.
          dispatch({ type: "sendFailed", message: baseMessage, definitive: true });
          return;
        }
        dispatch({ type: "sendFailed", message: `${baseMessage}${unconfirmedDeliverySuffix}`, definitive: false });
      } finally {
        sendInFlightRef.current = false;
      }
    })();
  }, [apiBaseUrl, connected]);

  return {
    state,
    setDraft: useCallback((draft: string) => dispatch({ type: "draftChanged", draft }), []),
    sendMessage,
    selectSession,
    startNewConversation,
    refreshSessions,
    retryMessages,
  };
}
