import { useCallback, useReducer } from "react";
import type { SelectedChatAttachment, SelectedUploadFile, UploadKind } from "../../shared/contracts";

import { chatThreadReducer, createInitialChatThreadState, createThreadId, type ChatThread, type ChatThreadId } from "../lib/chatThreads";
export type { ChatThread, ChatThreadId } from "../lib/chatThreads";

export interface ChatThreads {
  activeThread: ChatThread;
  createChat: () => void;
  replaceAttachments: (threadId: ChatThreadId, attachments: readonly SelectedChatAttachment[]) => void;
  selectChat: (threadId: ChatThreadId) => void;
  setInspectionFile: (threadId: ChatThreadId, kind: UploadKind, file?: SelectedUploadFile) => void;
  threads: readonly ChatThread[];
  updateDraft: (threadId: ChatThreadId, draft: string) => void;
}

export function useChatThreads(examplesEnabled: boolean): ChatThreads {
  // The caller supplies this only from the typed, trusted Electron desktop status.
  const [state, dispatch] = useReducer(chatThreadReducer, examplesEnabled, createInitialChatThreadState);
  const activeThread = state.threads.find((thread) => thread.id === state.activeThreadId) ?? state.threads[0]!;

  const selectChat = useCallback((threadId: ChatThreadId) => {
    dispatch({ type: "select", threadId });
  }, []);
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

  return { activeThread, createChat, replaceAttachments, selectChat, setInspectionFile, threads: state.threads, updateDraft };
}
