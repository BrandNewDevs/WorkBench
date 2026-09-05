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

export function createThreadId(): ChatThreadId {
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
  return [...threads].sort(compareThreads);
}

function compareThreads(left: ChatThread, right: ChatThread): number {
  return right.updatedAt - left.updatedAt || right.createdAt - left.createdAt || left.id.localeCompare(right.id);
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

  // Only one thread changed. The rest are already sorted, including when the
  // clock moves backwards or several updates share the same timestamp.
  const threads = state.threads.filter((candidate) => candidate.id !== threadId);
  const insertionIndex = threads.findIndex((candidate) => compareThreads(updatedThread, candidate) < 0);
  threads.splice(insertionIndex === -1 ? threads.length : insertionIndex, 0, updatedThread);
  return { ...state, threads };
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

export function createInitialChatThreadState(examplesEnabled: boolean): ChatThreadState {
  const now = Date.now();
  const initialThread = examplesEnabled
    ? createExampleChatThread(now)
    : createLocalChatThread(createThreadId(), now);
  return { threads: [initialThread], activeThreadId: initialThread.id };
}
