import type {
  ChatMessage,
  ChatSession,
  ChatSessionStatus,
  ChatStage,
  ChatWorkflowType,
  SelectedChatAttachment,
  SelectedUploadFile,
  SessionActivityEvent,
  UploadKind,
} from "../../shared/contracts";
import { chatStageSchema } from "../../shared/contracts.ts";

export type ChatThreadId = string & { readonly __chatThreadId: unique symbol };
export type ChatThreadSource = "example" | "local";
export type ChatSessionsState = "idle" | "loading" | "ready" | "error";
export type ChatMessagesState = "idle" | "loading" | "ready" | "error";
export type ChatSendState = "idle" | "sending" | "error";
export type ChatWorkflowState = "queued" | "processing" | "completed" | "failed" | "awaitingApproval";

interface ChatThreadFields {
  id: ChatThreadId;
  title: string;
  draft: string;
  attachments: readonly SelectedChatAttachment[];
  inspectionFiles: Partial<Record<UploadKind, SelectedUploadFile>>;
  createdAt: number;
  updatedAt: number;
  /** Backend workflow session; absent until FastAPI accepts this thread. */
  sessionId?: string;
  workflowType: ChatWorkflowType;
  stage?: ChatStage;
  status?: ChatSessionStatus;
  messages: readonly ChatMessage[];
  messagesState: ChatMessagesState;
  sendState: ChatSendState;
  sendError?: string;
  workflowState?: ChatWorkflowState;
  activityEvents: readonly SessionActivityEvent[];
  uploadedIdsByToken: Readonly<Record<string, string>>;
  streamError?: string;
  /** Client idempotency key of an unresolved append; retries reuse it. */
  pendingClientMessageId?: string;
  /** Client idempotency key of an unresolved session create; retries reuse it. */
  pendingClientSessionId?: string;
  /** Draft snapshot bound to the pending keys; retries resend it, not later edits. */
  pendingDraft?: string;
  /** True once a completed session list has included this session. */
  seenInSessions?: boolean;
}

export interface LocalChatThread extends ChatThreadFields {
  source: "local";
}

export interface ExampleChatThread extends ChatThreadFields {
  source: "example";
}

export type ChatThread = LocalChatThread | ExampleChatThread;

export interface ChatThreadState {
  threads: readonly ChatThread[];
  activeThreadId: ChatThreadId;
  sessionsState: ChatSessionsState;
}

export type ChatThreadAction =
  | { type: "select"; threadId: ChatThreadId }
  | { type: "create"; threadId: ChatThreadId; now: number }
  | { type: "updateDraft"; threadId: ChatThreadId; draft: string; now: number }
  | { type: "replaceAttachments"; threadId: ChatThreadId; attachments: readonly SelectedChatAttachment[]; now: number }
  | { type: "setInspectionFile"; threadId: ChatThreadId; file?: SelectedUploadFile; kind: UploadKind; now: number }
  | { type: "sessionsLoading" }
  | { type: "sessionsLoaded"; freshThreadId: ChatThreadId; now: number; sessions: readonly ChatSession[] }
  | { type: "sessionsFailed" }
  | { type: "messagesLoading"; threadId: ChatThreadId }
  | { type: "messagesLoaded"; threadId: ChatThreadId; messages: readonly ChatMessage[] }
  | { type: "messagesFailed"; threadId: ChatThreadId }
  | { type: "sessionBound"; threadId: ChatThreadId; session: ChatSession }
  | { type: "messageAppended"; threadId: ChatThreadId; message: ChatMessage; now: number }
  | { type: "uploadRegistered"; threadId: ChatThreadId; uploadToken: string; uploadId: string }
  | { type: "workflowQueued"; threadId: ChatThreadId }
  | { type: "workflowEvent"; threadId: ChatThreadId; event: SessionActivityEvent }
  | { type: "streamConnected"; threadId: ChatThreadId }
  | { type: "streamFailed"; threadId: ChatThreadId; message: string }
  | { type: "sessionSynced"; threadId: ChatThreadId; session: ChatSession }
  | { type: "sendStarted"; threadId: ChatThreadId; clientMessageId: string; clientSessionId?: string; draft: string }
  | { type: "sendFailed"; threadId: ChatThreadId; message: string; definitive: boolean }
  | { type: "sendResolved"; threadId: ChatThreadId; now: number }
  | { type: "draftClearedIfUnchanged"; threadId: ChatThreadId; draft: string; now: number };

let localThreadSequence = 0;

export function createThreadId(): ChatThreadId {
  localThreadSequence += 1;
  const randomId = globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${localThreadSequence}`;
  return `local-chat-${randomId}` as ChatThreadId;
}

/** The renderer may build identifiers only from server-issued session IDs. */
export function chatThreadIdForSession(sessionId: string): ChatThreadId {
  return `chat-${sessionId}` as ChatThreadId;
}

export function chatThreadFromSession(session: ChatSession): LocalChatThread {
  return {
    id: chatThreadIdForSession(session.sessionId),
    sessionId: session.sessionId,
    title: session.title,
    source: "local",
    workflowType: session.workflowType,
    stage: session.stage,
    status: session.status,
    draft: "",
    attachments: [],
    inspectionFiles: {},
    messages: [],
    messagesState: "idle",
    sendState: "idle",
    activityEvents: [],
    uploadedIdsByToken: {},
    seenInSessions: true,
    createdAt: Date.parse(session.createdAt),
    updatedAt: Date.parse(session.updatedAt),
  };
}

/** A local thread with unsent content survives a session refresh; a pristine one does not. */
export function threadHasUnsentContent(thread: ChatThread): boolean {
  return (
    thread.draft.trim().length > 0 ||
    (thread.pendingDraft?.trim().length ?? 0) > 0 ||
    // An unresolved session create may still bind to its committed session.
    thread.pendingClientSessionId !== undefined ||
    thread.attachments.length > 0 ||
    thread.inspectionFiles.inspectionReport !== undefined ||
    thread.inspectionFiles.sitePhotograph !== undefined
  );
}

/** Derive the backend session title from the first non-blank draft line. */
export function chatSessionTitleFromDraft(draft: string): string {
  const firstLine = draft.trim().split(/\r?\n/, 1)[0] ?? "";
  const condensed = firstLine.replace(/\s+/g, " ").trim();
  if (condensed.length === 0) return "Inspection review";
  return condensed.length > 80 ? `${condensed.slice(0, 80).trimEnd()}…` : condensed;
}

function isEmptyNewChat(thread: ChatThread): boolean {
  return (
    thread.source === "local" &&
    thread.sessionId === undefined &&
    thread.title === "New chat" &&
    !threadHasUnsentContent(thread)
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

function replaceThreads(state: ChatThreadState, threads: readonly ChatThread[]): ChatThreadState {
  const activeThread = threads.find((thread) => thread.id === state.activeThreadId);
  if (activeThread) return { ...state, threads, sessionsState: "ready" };
  const fallback = threads[0];
  if (!fallback) return state;
  return { ...state, threads, activeThreadId: fallback.id, sessionsState: "ready" };
}

/**
 * Refresh a bound thread from backend state without discarding local state:
 * the identifier stays stable so in-flight callbacks keep targeting this
 * thread, and drafts, files, messages, and send state survive the refresh.
 */
export function mergeBackendThread(existing: ChatThread, backend: LocalChatThread): ChatThread {
  return {
    ...backend,
    id: existing.id,
    title: existing.title === "New chat" ? backend.title : existing.title,
    draft: existing.draft,
    attachments: existing.attachments,
    inspectionFiles: existing.inspectionFiles,
    messages: existing.messages,
    messagesState: existing.messagesState,
    sendState: existing.sendState,
    sendError: existing.sendError,
    workflowState: existing.workflowState,
    activityEvents: existing.activityEvents,
    uploadedIdsByToken: existing.uploadedIdsByToken,
    streamError: existing.streamError,
    pendingClientMessageId: existing.pendingClientMessageId,
    pendingClientSessionId: existing.pendingClientSessionId,
    pendingDraft: existing.pendingDraft,
  };
}

/**
 * The stored message that resolves one append attempt, found by its
 * client idempotency key. Content never identifies a message: two
 * identical texts are different appends.
 */
export function findDeliveredMessage(
  stored: readonly ChatMessage[],
  clientMessageId: string,
): ChatMessage | undefined {
  return stored.find((message) => message.clientMessageId === clientMessageId);
}

/**
 * Merge a fetched message list into the local conversation. The backend
 * store is append-only, so absence from an older snapshot is not deletion:
 * a send that completed while the list request was in flight stays in the
 * merged array. Fetching without local extras preserves the server order.
 */
export function mergeLoadedMessages(
  local: readonly ChatMessage[],
  fetched: readonly ChatMessage[],
): readonly ChatMessage[] {
  const fetchedIds = new Set(fetched.map((message) => message.messageId));
  const extras = local.filter((message) => !fetchedIds.has(message.messageId));
  if (extras.length === 0) return fetched;
  return [...fetched, ...extras].sort(
    (left, right) =>
      Date.parse(left.createdAt) - Date.parse(right.createdAt) ||
      left.messageId.localeCompare(right.messageId),
  );
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
      return { threads: orderThreads([thread, ...state.threads]), activeThreadId: thread.id, sessionsState: state.sessionsState };
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
    case "sessionsLoading":
      return state.sessionsState === "loading" ? state : { ...state, sessionsState: "loading" };
    case "sessionsLoaded": {
      // Backend sessions are the truth for stage and status. Existing threads
      // merge by session ID so unsent content and history survive a refresh.
      // Plain-chat sessions belong to the Local Qwen chat mode; the workflow
      // workspace must not claim them or append workflow turns to their
      // shared history.
      const workflowSessions = action.sessions.filter(
        (session) => session.workflowType !== "localConversation",
      );
      const backendThreads = workflowSessions.map(chatThreadFromSession);
      const backendBySessionId = new Map(backendThreads.map((thread) => [thread.sessionId, thread]));
      // A create that committed while its response was lost shows up here by
      // its client key; the refresh rebinds the draft thread to it instead of
      // leaving a duplicate empty conversation beside the unsent draft.
      const replayedSessionIds = new Map(workflowSessions.map((session) => [session.clientSessionId, session.sessionId]));
      const keptThreads: ChatThread[] = [];
      for (const existing of state.threads) {
        if (existing.source === "example") {
          keptThreads.push(existing);
          continue;
        }
        const backend =
          existing.sessionId === undefined ? undefined : backendBySessionId.get(existing.sessionId);
        if (backend) {
          backendBySessionId.delete(backend.sessionId);
          keptThreads.push(mergeBackendThread(existing, backend));
          continue;
        }
        if (existing.sessionId !== undefined) {
          // A list captured before a concurrent first bind predates the
          // session, so absence alone is not proof of removal. A bound
          // thread leaves the list only after a completed list previously
          // included it and it holds no live send or unsent content.
          const hasLiveState = threadHasUnsentContent(existing) || existing.sendState === "sending";
          if (existing.seenInSessions === true && !hasLiveState) {
            continue;
          }
          keptThreads.push(existing);
          continue;
        }
        const replayedSessionId =
          existing.pendingClientSessionId === undefined
            ? undefined
            : replayedSessionIds.get(existing.pendingClientSessionId);
        if (replayedSessionId !== undefined) {
          const replayed = backendBySessionId.get(replayedSessionId);
          if (replayed !== undefined) {
            backendBySessionId.delete(replayed.sessionId);
            keptThreads.push({
              ...mergeBackendThread(existing, replayed),
              pendingClientSessionId: undefined,
              seenInSessions: true,
            });
            continue;
          }
        }
        // Unbound threads survive a refresh only while they hold unsent content.
        if (threadHasUnsentContent(existing)) {
          keptThreads.push(existing);
        }
      }
      const merged = orderThreads([...keptThreads, ...backendBySessionId.values()]);
      if (merged.length === 0) {
        const freshThread = createLocalChatThread(action.freshThreadId, action.now);
        return { threads: [freshThread], activeThreadId: freshThread.id, sessionsState: "ready" };
      }
      return replaceThreads(state, merged);
    }
    case "sessionsFailed":
      return { ...state, sessionsState: "error" };
    case "messagesLoading":
      return updateThread(state, action.threadId, (thread) =>
        thread.messagesState === "loading" ? thread : { ...thread, messagesState: "loading" },
      );
    case "messagesLoaded":
      return updateThread(state, action.threadId, (thread) => ({
        ...thread,
        messages: mergeLoadedMessages(thread.messages, action.messages),
        messagesState: "ready",
      }));
    case "messagesFailed":
      return updateThread(state, action.threadId, (thread) =>
        thread.messagesState === "loading" ? { ...thread, messagesState: "error" } : thread,
      );
    case "sessionBound":
      return updateThread(state, action.threadId, (thread) => ({
        ...thread,
        sessionId: action.session.sessionId,
        stage: action.session.stage,
        status: action.session.status,
        title: thread.title === "New chat" ? action.session.title : thread.title,
        // The create resolved, so its key has served its replay purpose.
        pendingClientSessionId: undefined,
        updatedAt: Date.parse(action.session.updatedAt),
      }));
    case "messageAppended":
      return updateThread(state, action.threadId, (thread) => ({
        ...thread,
        messages: [...thread.messages, action.message],
        messagesState: "ready",
        sendState: "idle",
        pendingClientMessageId: undefined,
        pendingClientSessionId: undefined,
        pendingDraft: undefined,
        updatedAt: action.now,
      }));
    case "uploadRegistered":
      return updateThread(state, action.threadId, (thread) => ({
        ...thread,
        uploadedIdsByToken: { ...thread.uploadedIdsByToken, [action.uploadToken]: action.uploadId },
      }));
    case "workflowQueued":
      return updateThread(state, action.threadId, (thread) => ({
        ...thread,
        workflowState: "queued",
        streamError: undefined,
      }));
    case "workflowEvent":
      return updateThread(state, action.threadId, (thread) => {
        if (thread.activityEvents.some((event) => event.eventId === action.event.eventId)) return thread;
        let workflowState = thread.workflowState;
        let stage = thread.stage;
        let status = thread.status;
        if (action.event.eventType === "message.accepted") workflowState = "queued";
        if (action.event.eventType === "workflow.progress") workflowState = "processing";
        if (action.event.eventType === "message.completed") workflowState = "completed";
        if (action.event.eventType === "approval.required") workflowState = "awaitingApproval";
        if (action.event.eventType === "workflow.failed") {
          workflowState = "failed";
          stage = "failed";
          status = "failed";
        }
        if (action.event.eventType === "workflow.stageChanged") {
          const parsedStage = chatStageSchema.safeParse(action.event.payload.stage);
          if (parsedStage.success) stage = parsedStage.data;
          const runStatus = action.event.payload.status;
          workflowState = runStatus === "queued" ? "queued" : runStatus === "waitingForApproval" ? "awaitingApproval" : runStatus === "completed" ? "completed" : runStatus === "failed" ? "failed" : "processing";
          if (runStatus === "completed") status = "completed";
          if (runStatus === "failed") status = "failed";
          if (runStatus === "approvalRejected") status = "approvalRejected";
        }
        return {
          ...thread,
          activityEvents: [...thread.activityEvents, action.event],
          workflowState,
          stage,
          status,
          streamError: undefined,
          updatedAt: Date.parse(action.event.occurredAt),
        };
      });
    case "streamFailed":
      return updateThread(state, action.threadId, (thread) => ({ ...thread, streamError: action.message }));
    case "streamConnected":
      return updateThread(state, action.threadId, (thread) =>
        thread.streamError === undefined ? thread : { ...thread, streamError: undefined },
      );
    case "sessionSynced":
      return updateThread(state, action.threadId, (thread) => ({
        ...thread,
        stage: action.session.stage,
        status: action.session.status,
        updatedAt: Date.parse(action.session.updatedAt),
      }));
    case "sendStarted":
      return updateThread(state, action.threadId, (thread) =>
        thread.sendState === "sending" && thread.pendingClientMessageId === action.clientMessageId
          ? thread
          : {
              ...thread,
              sendState: "sending",
              sendError: undefined,
              pendingClientMessageId: action.clientMessageId,
              // A retry keeps each snapshot its key was created for; the
              // current draft may have been edited after the failure.
              pendingClientSessionId: thread.pendingClientSessionId ?? action.clientSessionId,
              pendingDraft: thread.pendingDraft ?? action.draft,
            },
      );
    case "sendFailed":
      return updateThread(state, action.threadId, (thread) => ({
        ...thread,
        sendState: "error",
        sendError: action.message,
        // A definitive failure proved nothing was stored, so the next send
        // starts fresh. An ambiguous failure keeps its keys and bound draft
        // so a retry of the same send replays idempotently on FastAPI and
        // later edits stay out of the retried payload.
        pendingClientMessageId: action.definitive ? undefined : thread.pendingClientMessageId,
        pendingClientSessionId: action.definitive ? undefined : thread.pendingClientSessionId,
        pendingDraft: action.definitive ? undefined : thread.pendingDraft,
      }));
    case "sendResolved":
      return updateThread(state, action.threadId, (thread) =>
        thread.sendState === "idle" &&
        thread.sendError === undefined &&
        thread.pendingClientMessageId === undefined &&
        thread.pendingClientSessionId === undefined &&
        thread.pendingDraft === undefined
          ? thread
          : {
              ...thread,
              sendState: "idle",
              sendError: undefined,
              pendingClientMessageId: undefined,
              pendingClientSessionId: undefined,
              pendingDraft: undefined,
              updatedAt: action.now,
            },
      );
    case "draftClearedIfUnchanged":
      return updateThread(state, action.threadId, (thread) =>
        thread.draft.length > 0 && thread.draft === action.draft
          ? { ...thread, draft: "", updatedAt: action.now }
          : thread,
      );
  }
}

function createLocalChatThread(threadId: ChatThreadId, now: number): LocalChatThread {
  return {
    id: threadId,
    title: "New chat",
    source: "local",
    workflowType: "inspectionAnalysis",
    draft: "",
    attachments: [],
    inspectionFiles: {},
    messages: [],
    messagesState: "idle",
    sendState: "idle",
    activityEvents: [],
    uploadedIdsByToken: {},
    createdAt: now,
    updatedAt: now,
  };
}

function createExampleChatThread(now: number): ExampleChatThread {
  return {
    id: "example-inspection-report-review" as ChatThreadId,
    title: "Inspection report review",
    source: "example",
    workflowType: "inspectionAnalysis",
    draft: "",
    attachments: [],
    inspectionFiles: {},
    messages: [],
    messagesState: "idle",
    sendState: "idle",
    activityEvents: [],
    uploadedIdsByToken: {},
    createdAt: now,
    updatedAt: now,
  };
}

export function createInitialChatThreadState(examplesEnabled: boolean): ChatThreadState {
  const now = Date.now();
  const initialThread = examplesEnabled
    ? createExampleChatThread(now)
    : createLocalChatThread(createThreadId(), now);
  return { threads: [initialThread], activeThreadId: initialThread.id, sessionsState: examplesEnabled ? "idle" : "loading" };
}

export interface ChatStageStep {
  stage: ChatStage;
  state: "done" | "active" | "failed" | "queued";
}

const inspectionStageOrder: readonly ChatStage[] = [
  "collectingInputs",
  "extracting",
  "retrieving",
  "drafting",
  "validating",
  "awaitingApproval",
  "exporting",
];

const codeRepairStageOrder: readonly ChatStage[] = [
  "collectingInputs",
  "planning",
  "awaitingApproval",
  "sandboxExecuting",
  "repairing",
];

/**
 * Renderable stage progress for one backend session. Terminal rejection and
 * unknown stage positions stay out of the pipeline; the status chip shows them.
 */
export function chatStageSteps(
  workflowType: ChatWorkflowType,
  stage: ChatStage,
  status: ChatSessionStatus,
): readonly ChatStageStep[] {
  const order = workflowType === "codeRepair" ? codeRepairStageOrder : inspectionStageOrder;
  const currentIndex = order.indexOf(stage);
  if (status === "approvalRejected" || currentIndex < 0) {
    return [];
  }
  if (status === "failed") {
    return order.map((step, index) => ({
      stage: step,
      state: index < currentIndex ? ("done" as const) : index === currentIndex ? ("failed" as const) : ("queued" as const),
    }));
  }
  return order.map((step, index) => ({
    stage: step,
    state: index < currentIndex ? ("done" as const) : index === currentIndex ? ("active" as const) : ("queued" as const),
  }));
}

export const chatStageLabels: Record<ChatStage, string> = {
  collectingInputs: "Collecting inputs",
  extracting: "Extraction",
  retrieving: "Retrieval",
  drafting: "Drafting",
  validating: "Validation",
  planning: "Planning",
  awaitingApproval: "Awaiting approval",
  exporting: "Export",
  sandboxExecuting: "Sandbox execution",
  repairing: "Repair",
  approvalRejected: "Approval rejected",
  completed: "Completed",
  failed: "Failed",
};

export const chatSessionStatusLabel: Record<ChatSessionStatus, string> = {
  active: "Active",
  completed: "Completed",
  failed: "Failed",
  approvalRejected: "Approval rejected",
};
