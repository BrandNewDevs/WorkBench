import { useCallback, useEffect, useReducer, useRef } from "react";
import type { SelectedChatAttachment, SelectedUploadFile, UploadKind } from "../../shared/contracts";

import { LocalApiError, apiFailureWasDefinitive, localApi } from "../api/localApi";
import {
  chatSessionTitleFromDraft,
  chatThreadReducer,
  createInitialChatThreadState,
  createThreadId,
  findDeliveredMessage,
  type ChatThread,
  type ChatThreadId,
  type ChatThreadState,
} from "../lib/chatThreads";
export type { ChatThread, ChatThreadId } from "../lib/chatThreads";

export interface ChatThreadsOptions {
  apiBaseUrl: string;
  /** True only for a verified local FastAPI employee session. */
  connected: boolean;
  examplesEnabled: boolean;
}

export interface ChatThreads {
  activeThread: ChatThread;
  createChat: () => void;
  refreshSessions: () => void;
  replaceAttachments: (threadId: ChatThreadId, attachments: readonly SelectedChatAttachment[]) => void;
  retryThreadMessages: (threadId: ChatThreadId) => void;
  selectChat: (threadId: ChatThreadId) => void;
  sendMessage: (threadId: ChatThreadId) => void;
  sessionsState: ChatThreadState["sessionsState"];
  setInspectionFile: (threadId: ChatThreadId, kind: UploadKind, file?: SelectedUploadFile) => void;
  threads: readonly ChatThread[];
  updateDraft: (threadId: ChatThreadId, draft: string) => void;
}

function sendFailureMessage(error: unknown): string {
  if (error instanceof LocalApiError) {
    if (error.kind === "unauthorized") return "Your local employee session could not be verified. Sign in again.";
    if (error.kind === "timeout") return "The local service timed out. Delivery is unconfirmed; sending again reuses the same request and cannot duplicate it.";
    if (error.kind === "network") return "FastAPI is unavailable. Delivery is unconfirmed; sending again reuses the same request and cannot duplicate it.";
    if (error.kind === "http" && error.status === 409) return "This chat session is closed and no longer accepts messages.";
    return error.message;
  }
  return "The message could not be sent.";
}

export function useChatThreads({ apiBaseUrl, connected, examplesEnabled }: ChatThreadsOptions): ChatThreads {
  const [state, dispatch] = useReducer(chatThreadReducer, examplesEnabled, createInitialChatThreadState);
  const stateRef = useRef<ChatThreadState>(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  const sessionsSequenceRef = useRef(0);
  const messageSequencesRef = useRef(new Map<ChatThreadId, number>());
  const sendSequencesRef = useRef(new Map<ChatThreadId, number>());
  // Reducer state commits a render behind the Send event, so it cannot stop
  // two invocations in the same tick from minting two idempotency keys. This
  // synchronous gate is authoritative for one send body per thread.
  const inFlightSendThreadsRef = useRef(new Set<ChatThreadId>());
  const eventSubscriptionsRef = useRef(new Map<string, () => void>());

  const loadSessions = useCallback(() => {
    const requestSequence = ++sessionsSequenceRef.current;
    dispatch({ type: "sessionsLoading" });
    void localApi.listChatSessions(apiBaseUrl).then(
      (response) => {
        if (sessionsSequenceRef.current !== requestSequence) return;
        dispatch({ type: "sessionsLoaded", freshThreadId: createThreadId(), now: Date.now(), sessions: response.sessions });
      },
      () => {
        if (sessionsSequenceRef.current !== requestSequence) return;
        dispatch({ type: "sessionsFailed" });
      },
    );
  }, [apiBaseUrl]);

  useEffect(() => {
    if (connected) {
      loadSessions();
    }
  }, [connected, loadSessions]);

  const loadThreadMessages = useCallback(
    (threadId: ChatThreadId) => {
      const thread = stateRef.current.threads.find((candidate) => candidate.id === threadId);
      if (!thread || thread.sessionId === undefined || thread.messagesState === "loading") return;
      const requestSequence = (messageSequencesRef.current.get(threadId) ?? 0) + 1;
      messageSequencesRef.current.set(threadId, requestSequence);
      dispatch({ type: "messagesLoading", threadId });
      void localApi.listChatMessages(thread.sessionId, apiBaseUrl).then(
        (response) => {
          if (messageSequencesRef.current.get(threadId) === requestSequence) {
            dispatch({ type: "messagesLoaded", threadId, messages: response.messages });
          }
        },
        () => {
          if (messageSequencesRef.current.get(threadId) === requestSequence) {
            dispatch({ type: "messagesFailed", threadId });
          }
        },
      );
    },
    [apiBaseUrl],
  );

  const activeThread = state.threads.find((thread) => thread.id === state.activeThreadId) ?? state.threads[0]!;

  // Backend-backed threads fetch their persisted messages when they first become active.
  useEffect(() => {
    if (connected && activeThread.sessionId !== undefined && activeThread.messagesState === "idle") {
      loadThreadMessages(activeThread.id);
    }
  }, [activeThread, connected, loadThreadMessages]);

  useEffect(() => {
    if (!connected) {
      for (const unsubscribe of eventSubscriptionsRef.current.values()) unsubscribe();
      eventSubscriptionsRef.current.clear();
      return;
    }
    const bound = new Map(
      state.threads.flatMap((thread) => thread.source === "local" && thread.sessionId && thread.status === "active" ? [[thread.sessionId, thread.id] as const] : []),
    );
    for (const [sessionId, unsubscribe] of eventSubscriptionsRef.current) {
      if (!bound.has(sessionId)) {
        unsubscribe();
        eventSubscriptionsRef.current.delete(sessionId);
      }
    }
    for (const [sessionId, threadId] of bound) {
      if (eventSubscriptionsRef.current.has(sessionId)) continue;
      const unsubscribe = window.workbench.subscribeSessionEvents(sessionId, (update) => {
        if (update.type === "event") {
          dispatch({ type: "workflowEvent", threadId, event: update.event });
          if (update.event.eventType === "message.completed" || update.event.eventType === "workflow.failed") {
            void localApi.listChatMessages(sessionId, apiBaseUrl).then(
              (response) => dispatch({ type: "messagesLoaded", threadId, messages: response.messages }),
              () => undefined,
            );
            void localApi.getChatSession(sessionId, apiBaseUrl).then(
              (session) => dispatch({ type: "sessionSynced", threadId, session }),
              () => undefined,
            );
          }
        } else if (update.type === "error") {
          dispatch({ type: "streamFailed", threadId, message: update.message });
        }
      });
      eventSubscriptionsRef.current.set(sessionId, unsubscribe);
    }
  }, [apiBaseUrl, connected, state.threads]);

  useEffect(() => () => {
    for (const unsubscribe of eventSubscriptionsRef.current.values()) unsubscribe();
    eventSubscriptionsRef.current.clear();
  }, []);

  const selectChat = useCallback(
    (threadId: ChatThreadId) => {
      dispatch({ type: "select", threadId });
      loadThreadMessages(threadId);
    },
    [loadThreadMessages],
  );

  const retryThreadMessages = useCallback(
    (threadId: ChatThreadId) => {
      loadThreadMessages(threadId);
    },
    [loadThreadMessages],
  );

  const createChat = useCallback(() => {
    const threadId = createThreadId();
    const now = Date.now();
    dispatch({ type: "create", threadId, now });
  }, []);

  const updateDraft = useCallback((threadId: ChatThreadId, draft: string) => {
    const now = Date.now();
    dispatch({ type: "updateDraft", threadId, draft, now });
  }, []);

  const replaceAttachments = useCallback((threadId: ChatThreadId, attachments: readonly SelectedChatAttachment[]) => {
    const now = Date.now();
    dispatch({ type: "replaceAttachments", threadId, attachments, now });
  }, []);

  const setInspectionFile = useCallback((threadId: ChatThreadId, kind: UploadKind, file?: SelectedUploadFile) => {
    const now = Date.now();
    dispatch({ type: "setInspectionFile", threadId, kind, file, now });
  }, []);

  const sendMessage = useCallback(
    (threadId: ChatThreadId) => {
      const thread = stateRef.current.threads.find((candidate) => candidate.id === threadId);
      if (!thread || thread.source === "example" || thread.sendState === "sending") return;
      // One idempotency key per unresolved append, bound to the draft snapshot
      // it was created for: retries reuse both, so FastAPI can never store the
      // message twice and an edited composer draft is never mistaken for the
      // pending payload.
      const submittedDraft = thread.pendingDraft ?? thread.draft;
      const content = submittedDraft.trim();
      if (content.length === 0) return;
      if (thread.status !== undefined && thread.status !== "active") return;
      if (inFlightSendThreadsRef.current.has(threadId)) return;
      inFlightSendThreadsRef.current.add(threadId);

      const requestSequence = (sendSequencesRef.current.get(threadId) ?? 0) + 1;
      sendSequencesRef.current.set(threadId, requestSequence);
      const clientMessageId = thread.pendingClientMessageId ?? globalThis.crypto.randomUUID();
      // One idempotency key per unresolved session create: a lost create
      // response keeps it, so the retry replays the committed session on
      // FastAPI instead of storing an empty duplicate conversation.
      const clientSessionId =
        thread.sessionId === undefined
          ? (thread.pendingClientSessionId ?? globalThis.crypto.randomUUID())
          : undefined;
      dispatch({ type: "sendStarted", threadId, clientMessageId, clientSessionId, draft: submittedDraft });
      void (async () => {
        let sessionId = thread.sessionId;
        try {
          if (sessionId === undefined) {
            const created = await localApi.createChatSession(
              {
                workflowType: thread.workflowType,
                title: chatSessionTitleFromDraft(content),
                clientSessionId,
              },
              apiBaseUrl,
            );
            if (sendSequencesRef.current.get(threadId) !== requestSequence) return;
            dispatch({ type: "sessionBound", threadId, session: created });
            sessionId = created.sessionId;
          }
          const selectedFiles = [
            ...Object.values(thread.inspectionFiles).filter((file): file is SelectedUploadFile => file !== undefined),
            ...thread.attachments,
          ];
          const uploadedIdsByToken = { ...thread.uploadedIdsByToken };
          for (const file of selectedFiles) {
            if (uploadedIdsByToken[file.uploadToken]) continue;
            const uploaded = await localApi.uploadWorkflowFile(sessionId, file.uploadToken);
            uploadedIdsByToken[file.uploadToken] = uploaded.uploadId;
            dispatch({ type: "uploadRegistered", threadId, uploadToken: file.uploadToken, uploadId: uploaded.uploadId });
          }
          const accepted = await localApi.appendChatMessage(
            sessionId,
            { content, clientMessageId, selectedUploadIds: selectedFiles.map((file) => uploadedIdsByToken[file.uploadToken]!) },
            apiBaseUrl,
          );
          if (sendSequencesRef.current.get(threadId) !== requestSequence) return;
          dispatch({
            type: "messageAppended",
            threadId,
            message: {
              messageId: accepted.messageId,
              sessionId,
              authorUserId: null,
              role: "user",
              content,
              createdAt: new Date().toISOString(),
              clientMessageId,
            },
            now: Date.now(),
          });
          dispatch({ type: "workflowQueued", threadId });
          dispatch({ type: "draftClearedIfUnchanged", threadId, draft: submittedDraft, now: Date.now() });
        } catch (error) {
          if (sendSequencesRef.current.get(threadId) !== requestSequence) return;
          // Release the keys only when the outcome is certain: FastAPI
          // answered and refused before any write, so nothing was stored.
          // A lost create response no longer releases anything: the pending
          // session key replays the committed session, and a lost append
          // response keeps its key for the same reason as before. Ambiguous
          // failures keep the keys and draft until a retry resolves them.
          if (apiFailureWasDefinitive(error)) {
            dispatch({ type: "sendFailed", threadId, message: sendFailureMessage(error), definitive: true });
            return;
          }
          if (sessionId !== undefined) {
            try {
              const stored = await localApi.listChatMessages(sessionId, apiBaseUrl);
              if (sendSequencesRef.current.get(threadId) !== requestSequence) return;
              if (findDeliveredMessage(stored.messages, clientMessageId) !== undefined) {
                dispatch({ type: "messagesLoaded", threadId, messages: stored.messages });
                dispatch({ type: "draftClearedIfUnchanged", threadId, draft: submittedDraft, now: Date.now() });
                dispatch({ type: "sendResolved", threadId, now: Date.now() });
                return;
              }
            } catch {
              // Reconciliation failed too; the append error stays visible.
            }
          }
          dispatch({ type: "sendFailed", threadId, message: sendFailureMessage(error), definitive: false });
        } finally {
          inFlightSendThreadsRef.current.delete(threadId);
        }
      })();
    },
    [apiBaseUrl],
  );

  return {
    activeThread,
    createChat,
    refreshSessions: loadSessions,
    replaceAttachments,
    retryThreadMessages,
    selectChat,
    sendMessage,
    sessionsState: state.sessionsState,
    setInspectionFile,
    threads: state.threads,
    updateDraft,
  };
}
