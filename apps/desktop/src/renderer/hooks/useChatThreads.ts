import { useCallback, useReducer } from "react";
import type { SelectedChatAttachment, SelectedUploadFile, UploadKind } from "../../shared/contracts";

export type ChatThreadId = string & { readonly __chatThreadId: unique symbol };
export type ChatThreadSource = "example" | "local";

interface ChatThreadFields {
  id: ChatThreadId;
  title: string;
  draft: string;
  attachments: readonly SelectedChatAttachment[];
  inspectionFiles: Partial<Record<UploadKind, SelectedUploadFile>>;
  createdAt: number;
  updatedAt: number;
}

export interface LocalChatThread extends ChatThreadFields {
  source: "local";
}

export interface ExampleChatThread extends ChatThreadFields {
  source: "example";
}

export type ChatThread = LocalChatThread | ExampleChatThread;

interface ChatThreadState {
  threads: readonly ChatThread[];
  activeThreadId: ChatThreadId;
}

type ChatThreadAction =
  | { type: "select"; threadId: ChatThreadId }
  | { type: "create"; threadId: ChatThreadId; now: number }
  | { type: "updateDraft"; threadId: ChatThreadId; draft: string; now: number }
  | { type: "replaceAttachments"; threadId: ChatThreadId; attachments: readonly SelectedChatAttachment[]; now: number }
  | { type: "setInspectionFile"; threadId: ChatThreadId; file?: SelectedUploadFile; kind: UploadKind; now: number };

let localThreadSequence = 0;

function createThreadId(): ChatThreadId {
  localThreadSequence += 1;
  const randomId = globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${localThreadSequence}`;
  return `local-chat-${randomId}` as ChatThreadId;
}

function isEmptyNewChat(thread: ChatThread): boolean {
  return (
    thread.source === "local" &&
    thread.title === "New chat" &&
    thread.draft.length === 0 &&
    thread.attachments.length === 0 &&
    thread.inspectionFiles.inspectionReport === undefined &&
    thread.inspectionFiles.sitePhotograph === undefined
  );
}

function orderThreads(threads: readonly ChatThread[]): readonly ChatThread[] {
  return [...threads].sort(
    (left, right) => right.updatedAt - left.updatedAt || right.createdAt - left.createdAt || left.id.localeCompare(right.id),
  );
}

function updateThread(
  state: ChatThreadState,
  threadId: ChatThreadId,
  update: (thread: ChatThread) => ChatThread,
): ChatThreadState {
  const thread = state.threads.find((candidate) => candidate.id === threadId);
  if (!thread) return state;

  const updatedThread = update(thread);
  if (updatedThread === thread) return state;

  return {
    ...state,
    threads: orderThreads(state.threads.map((candidate) => (candidate.id === threadId ? updatedThread : candidate))),
  };
}

export function chatThreadReducer(state: ChatThreadState, action: ChatThreadAction): ChatThreadState {
  switch (action.type) {
    case "select":
      return state.threads.some((thread) => thread.id === action.threadId)
        ? { ...state, activeThreadId: action.threadId }
        : state;
    case "create": {
      const emptyThread = state.threads.find(isEmptyNewChat);
      if (emptyThread) return { ...state, activeThreadId: emptyThread.id };

      const thread = createLocalChatThread(action.threadId, action.now);
      return { threads: orderThreads([thread, ...state.threads]), activeThreadId: thread.id };
    }
    case "updateDraft":
      return updateThread(state, action.threadId, (thread) =>
        thread.draft === action.draft ? thread : { ...thread, draft: action.draft, updatedAt: action.now },
      );
    case "replaceAttachments":
      return updateThread(state, action.threadId, (thread) => ({
        ...thread,
        attachments: action.attachments,
        updatedAt: action.now,
      }));
    case "setInspectionFile":
      return updateThread(state, action.threadId, (thread) => {
        if (thread.inspectionFiles[action.kind] === action.file) return thread;
        return {
          ...thread,
          inspectionFiles: { ...thread.inspectionFiles, [action.kind]: action.file },
          updatedAt: action.now,
        };
      });
  }
}

function createLocalChatThread(threadId: ChatThreadId, now: number): LocalChatThread {
  return {
    id: threadId,
    title: "New chat",
    source: "local",
    draft: "",
    attachments: [],
    inspectionFiles: {},
    createdAt: now,
    updatedAt: now,
  };
}

function createExampleChatThread(now: number): ExampleChatThread {
  return {
    id: "example-inspection-report-review" as ChatThreadId,
    title: "Inspection report review",
    source: "example",
    draft: "",
    attachments: [],
    inspectionFiles: {},
    createdAt: now,
    updatedAt: now,
  };
}

function createInitialChatThreadState(examplesEnabled: boolean): ChatThreadState {
  const now = Date.now();
  const initialThread = examplesEnabled
    ? createExampleChatThread(now)
    : createLocalChatThread(createThreadId(), now);
  return { threads: [initialThread], activeThreadId: initialThread.id };
}

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
