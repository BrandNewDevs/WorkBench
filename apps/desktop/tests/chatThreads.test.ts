import assert from "node:assert/strict";
import { test } from "node:test";
import type { ChatMessage, ChatSession } from "../src/shared/contracts.ts";
import {
  chatSessionTitleFromDraft,
  chatStageSteps,
  chatThreadReducer,
  chatThreadFromSession,
  findDeliveredMessage,
  threadHasUnsentContent,
  type ChatThread,
  type ChatThreadId,
  type ChatThreadState,
} from "../src/renderer/lib/chatThreads.ts";

function thread(id: string, updatedAt: number, createdAt = 0): ChatThread {
  return {
    id: id as ChatThreadId, title: id, source: "local", workflowType: "inspectionAnalysis",
    draft: "", attachments: [], inspectionFiles: {},
    messages: [], messagesState: "idle", sendState: "idle", activityEvents: [], uploadedIdsByToken: {},
    createdAt, updatedAt,
  };
}

function stateOf(threads: readonly ChatThread[], activeThreadId: ChatThreadId, sessionsState: ChatThreadState["sessionsState"] = "ready"): ChatThreadState {
  return { threads, activeThreadId, sessionsState };
}

function session(sessionId: string, overrides: Partial<ChatSession> = {}): ChatSession {
  return {
    sessionId,
    ownerUserId: "00000000-0000-4000-8000-000000000000",
    workflowType: "inspectionAnalysis",
    title: "Report review",
    stage: "collectingInputs",
    status: "active",
    createdAt: "2026-09-01T10:00:00Z",
    updatedAt: "2026-09-01T10:05:00Z",
    clientSessionId: null,
    ...overrides,
  };
}

function message(content: string, createdAt = "2026-09-01T10:06:00Z"): ChatMessage {
  return {
    messageId: "11111111-1111-4111-8111-111111111111",
    sessionId: "22222222-2222-4222-8222-222222222222",
    authorUserId: null,
    role: "user",
    content,
    createdAt,
    clientMessageId: "99999999-9999-4999-8999-999999999999",
  };
}

test("edits preserve ordering, other drafts, and the selected chat", () => {
  const first = thread("first", 30);
  const second = thread("second", 20);
  const third = thread("third", 10);
  const state = stateOf([first, second, third], first.id);
  const result = chatThreadReducer(state, { type: "updateDraft", threadId: third.id, draft: "Review", now: 40 });
  assert.deepEqual(result.threads.map(({ id }) => id), [third.id, first.id, second.id]);
  assert.equal(result.activeThreadId, first.id);
  assert.equal(result.threads[0]?.draft, "Review");
  assert.equal(result.threads[1], first);
  assert.equal(state.threads[2]?.draft, "");
  assert.equal(chatThreadReducer(result, { type: "updateDraft", threadId: third.id, draft: "Review", now: 50 }), result);
});

test("clock rollback and equal timestamps retain deterministic order", () => {
  const first = thread("a", 30, 3);
  const second = thread("b", 20, 2);
  const third = thread("c", 20, 2);
  const state = stateOf([first, second, third], first.id);
  const rolledBack = chatThreadReducer(state, { type: "updateDraft", threadId: first.id, draft: "Rollback", now: 10 });
  assert.deepEqual(rolledBack.threads.map(({ id }) => id), [second.id, third.id, first.id]);
  const tied = chatThreadReducer(state, { type: "updateDraft", threadId: third.id, draft: "Tie", now: 20 });
  assert.deepEqual(tied.threads.map(({ id }) => id), [first.id, second.id, third.id]);
});

test("new chat reuses an empty draft without replacing another chat's files", () => {
  const first = thread("first", 30);
  const state = stateOf([first], first.id);
  const created = chatThreadReducer(state, { type: "create", threadId: "new" as ChatThreadId, now: 40 });
  const reused = chatThreadReducer(created, { type: "create", threadId: "unused" as ChatThreadId, now: 50 });
  assert.equal(reused.threads.length, 2);
  assert.equal(reused.activeThreadId, "new");
  const withFile = chatThreadReducer(reused, {
    type: "setInspectionFile", threadId: first.id, kind: "inspectionReport", now: 60,
    file: { uploadToken: "10000000-0000-4000-8000-000000000000", name: "report.pdf", kind: "inspectionReport", mimeType: "application/pdf", sizeBytes: 42 },
  });
  assert.equal(withFile.threads[0]?.inspectionFiles.inspectionReport?.name, "report.pdf");
  assert.deepEqual(withFile.threads[1]?.inspectionFiles, {});
});

test("a bound thread is no longer reusable as an empty new chat", () => {
  const bound = { ...thread("bound", 30), title: "New chat", sessionId: "33333333-3333-4333-8333-333333333333" };
  const state = stateOf([bound], bound.id);
  const created = chatThreadReducer(state, { type: "create", threadId: "new" as ChatThreadId, now: 40 });
  assert.equal(created.threads.length, 2);
  assert.equal(created.activeThreadId, "new");
});

test("loaded sessions replace pristine local threads and map backend fields", () => {
  const pristine = thread("pristine", 30);
  const state = stateOf([pristine], pristine.id, "loading");
  const result = chatThreadReducer(state, {
    type: "sessionsLoaded", freshThreadId: "fresh" as ChatThreadId, now: 40,
    sessions: [session("44444444-4444-4444-8444-444444444444", { stage: "retrieving" })],
  });
  assert.equal(result.sessionsState, "ready");
  assert.equal(result.threads.length, 1);
  const loaded = result.threads[0]!;
  assert.equal(loaded.id, "chat-44444444-4444-4444-8444-444444444444");
  assert.equal(loaded.sessionId, "44444444-4444-4444-8444-444444444444");
  assert.equal(loaded.title, "Report review");
  assert.equal(loaded.stage, "retrieving");
  assert.equal(loaded.status, "active");
  assert.equal(loaded.messagesState, "idle");
  assert.equal(loaded.updatedAt, Date.parse("2026-09-01T10:05:00Z"));
  assert.equal(result.activeThreadId, loaded.id);
});

test("loaded sessions preserve local threads that still hold unsent content", () => {
  const dirty = { ...thread("dirty", 35), draft: "Unsent note" };
  const state = stateOf([dirty], dirty.id, "loading");
  const result = chatThreadReducer(state, {
    type: "sessionsLoaded", freshThreadId: "fresh" as ChatThreadId, now: 40,
    sessions: [session("44444444-4444-4444-8444-444444444444")],
  });
  assert.equal(result.threads.length, 2);
  assert.equal(result.threads.find((candidate) => candidate.id === dirty.id)?.draft, "Unsent note");
  assert.equal(result.activeThreadId, dirty.id);
});

test("loaded sessions with an empty list seed one fresh local chat", () => {
  const pristine = thread("pristine", 30);
  const state = stateOf([pristine], pristine.id, "loading");
  const result = chatThreadReducer(state, {
    type: "sessionsLoaded", freshThreadId: "fresh" as ChatThreadId, now: 40, sessions: [],
  });
  assert.equal(result.sessionsState, "ready");
  assert.equal(result.threads.length, 1);
  assert.equal(result.threads[0]?.id, "fresh");
  assert.equal(result.threads[0]?.title, "New chat");
  assert.equal(result.activeThreadId, "fresh");
});

test("loaded sessions keep the example thread and activate the selection", () => {
  const example: ChatThread = { ...thread("example-inspection-report-review", 50), source: "example" };
  const state = stateOf([example], example.id, "loading");
  const result = chatThreadReducer(state, {
    type: "sessionsLoaded", freshThreadId: "fresh" as ChatThreadId, now: 40,
    sessions: [session("44444444-4444-4444-8444-444444444444")],
  });
  assert.equal(result.threads.length, 2);
  assert.ok(result.threads.some((candidate) => candidate.source === "example"));
  assert.equal(result.activeThreadId, example.id);
});

test("message append completes a send and reorders by activity", () => {
  const bound = { ...thread("bound", 30), sessionId: "33333333-3333-4333-8333-333333333333", sendState: "sending" as const };
  const newer = thread("newer", 60);
  const state = stateOf([newer, bound], bound.id);
  const result = chatThreadReducer(state, {
    type: "messageAppended", threadId: bound.id, message: message("Extract finding one"), now: 70,
  });
  assert.deepEqual(result.threads.map(({ id }) => id), [bound.id, newer.id]);
  const sent = result.threads[0]!;
  assert.equal(sent.messages.length, 1);
  assert.equal(sent.messages[0]?.content, "Extract finding one");
  assert.equal(sent.messagesState, "ready");
  assert.equal(sent.sendState, "idle");
});

test("session sync and failure paths update only the targeted thread", () => {
  const bound = { ...thread("bound", 30), sessionId: "33333333-3333-4333-8333-333333333333", stage: "extracting" as const };
  const other = thread("other", 20);
  const state = stateOf([bound, other], bound.id);

  const started = chatThreadReducer(state, { type: "sendStarted", threadId: bound.id, clientMessageId: "dddddddd-dddd-4ddd-8ddd-dddddddddddd", draft: "" });
  assert.equal(started.threads[0]?.sendState, "sending");

  const synced = chatThreadReducer(started, {
    type: "sessionSynced", threadId: bound.id,
    session: session("33333333-3333-4333-8333-333333333333", { stage: "drafting", updatedAt: "2026-09-01T10:07:00Z" }),
  });
  assert.equal(synced.threads[0]?.stage, "drafting");
  assert.equal(synced.threads[0]?.status, "active");
  assert.equal(synced.threads[1], other);

  const failed = chatThreadReducer(started, { type: "sendFailed", threadId: bound.id, message: "FastAPI is unavailable. The message was not sent.", definitive: false });
  assert.equal(failed.threads[0]?.sendState, "error");
  assert.equal(failed.threads[0]?.sendError, "FastAPI is unavailable. The message was not sent.");
  assert.equal(failed.threads[1], other);

  const cleared = chatThreadReducer(failed, { type: "sendStarted", threadId: bound.id, clientMessageId: "dddddddd-dddd-4ddd-8ddd-dddddddddddd", draft: "" });
  assert.equal(cleared.threads[0]?.sendState, "sending");
  assert.equal(cleared.threads[0]?.sendError, undefined);
  assert.equal(chatThreadReducer(cleared, { type: "sendFailed", threadId: "unknown" as ChatThreadId, message: "ignored", definitive: true }), cleared);
});

test("message loads transition idle, loading, ready, and error without touching other threads", () => {
  const bound = { ...thread("bound", 30), sessionId: "33333333-3333-4333-8333-333333333333" };
  const other = thread("other", 20);
  const state = stateOf([bound, other], bound.id);

  const loading = chatThreadReducer(state, { type: "messagesLoading", threadId: bound.id });
  assert.equal(loading.threads[0]?.messagesState, "loading");
  assert.equal(chatThreadReducer(loading, { type: "messagesLoading", threadId: bound.id }), loading);

  const loaded = chatThreadReducer(loading, { type: "messagesLoaded", threadId: bound.id, messages: [message("Stored message")] });
  assert.equal(loaded.threads[0]?.messagesState, "ready");
  assert.equal(loaded.threads[0]?.messages.length, 1);

  const retryLoad = chatThreadReducer(loaded, { type: "messagesLoading", threadId: bound.id });
  const errored = chatThreadReducer(retryLoad, { type: "messagesFailed", threadId: bound.id });
  assert.equal(errored.threads[0]?.messagesState, "error");
  assert.equal(errored.threads[1], other);

  const failedOutsideLoad = chatThreadReducer(loaded, { type: "messagesFailed", threadId: bound.id });
  assert.equal(failedOutsideLoad.threads[0]?.messagesState, "ready");
  assert.equal(chatThreadReducer(state, { type: "messagesLoaded", threadId: "unknown" as ChatThreadId, messages: [] }), state);
});

test("a stale message load keeps sends that completed after its snapshot", () => {
  const bound = { ...thread("bound", 30), sessionId: "33333333-3333-4333-8333-333333333333", messagesState: "loading" as const };
  const older = message("Earlier message", "2026-09-01T10:05:00Z");
  const sent = { ...message("Sent during load", "2026-09-01T10:06:00Z"), messageId: "33333333-3333-4333-8333-333333333331" };
  const state = stateOf([bound], bound.id);

  const appended = chatThreadReducer(state, { type: "messageAppended", threadId: bound.id, message: sent, now: 40 });
  const loaded = chatThreadReducer(appended, { type: "messagesLoaded", threadId: bound.id, messages: [older] });
  // The snapshot predates the append, so the sent message survives the load.
  assert.deepEqual(loaded.threads[0]?.messages, [older, sent]);
  assert.equal(loaded.threads[0]?.messagesState, "ready");

  // A snapshot that already includes the sent message does not duplicate it.
  const refetched = chatThreadReducer(loaded, { type: "messagesLoaded", threadId: bound.id, messages: [older, sent] });
  assert.deepEqual(refetched.threads[0]?.messages, [older, sent]);
});

test("session binding adopts the backend title only for a new chat", () => {
  const fresh = thread("local-1", 30);
  fresh.title = "New chat";
  const renamed = { ...thread("local-2", 20), title: "Pump 4 seal review" };
  const state = stateOf([fresh, renamed], fresh.id);

  const boundFresh = chatThreadReducer(state, {
    type: "sessionBound", threadId: fresh.id,
    session: session("33333333-3333-4333-8333-333333333333", { title: "Pump 4 seal review" }),
  });
  assert.equal(boundFresh.threads.find((candidate) => candidate.id === fresh.id)?.title, "Pump 4 seal review");
  assert.equal(boundFresh.threads.find((candidate) => candidate.id === fresh.id)?.sessionId, "33333333-3333-4333-8333-333333333333");

  const boundRenamed = chatThreadReducer(state, {
    type: "sessionBound", threadId: renamed.id,
    session: session("44444444-4444-4444-8444-444444444444", { title: "Backend title" }),
  });
  assert.equal(boundRenamed.threads.find((candidate) => candidate.id === renamed.id)?.title, "Pump 4 seal review");
});

test("draft clearing submits the exact snapshot and keeps newer edits", () => {
  const first = { ...thread("first", 30), draft: "Send me" };
  const second = { ...thread("second", 20), draft: "Keep me" };
  const state = stateOf([first, second], first.id);
  const cleared = chatThreadReducer(state, { type: "draftClearedIfUnchanged", threadId: first.id, draft: "Send me", now: 40 });
  assert.equal(cleared.threads[0]?.draft, "");
  assert.equal(cleared.threads[1]?.draft, "Keep me");

  const edited = { ...cleared, threads: [{ ...cleared.threads[0]!, draft: "Send me and more" }, cleared.threads[1]!] };
  const kept = chatThreadReducer(edited, { type: "draftClearedIfUnchanged", threadId: first.id, draft: "Send me", now: 50 });
  assert.equal(kept.threads[0]?.draft, "Send me and more");
  assert.equal(chatThreadReducer(kept, { type: "draftClearedIfUnchanged", threadId: first.id, draft: "Send me", now: 60 }), kept);
  const finished = chatThreadReducer(kept, { type: "draftClearedIfUnchanged", threadId: first.id, draft: "Send me and more", now: 70 });
  assert.equal(finished.threads[0]?.draft, "");
});

test("session titles derive from the first draft line without splitting mid-word content", () => {
  assert.equal(chatSessionTitleFromDraft("Review pump 4\nsecond line"), "Review pump 4");
  assert.equal(chatSessionTitleFromDraft("   \n  "), "Inspection review");
  assert.equal(chatSessionTitleFromDraft("  Packed   spaces  here  "), "Packed spaces here");
  const long = "A".repeat(120);
  assert.equal(chatSessionTitleFromDraft(long), `${"A".repeat(80)}…`);
});

test("unsent-content detection drives preservation", () => {
  assert.equal(threadHasUnsentContent(thread("empty", 10)), false);
  assert.equal(threadHasUnsentContent({ ...thread("draft", 10), draft: " note " }), true);
  assert.equal(threadHasUnsentContent({ ...thread("pending", 10), pendingDraft: "Unresolved append" }), true);
  assert.equal(threadHasUnsentContent({ ...thread("creating", 10), pendingClientSessionId: "dddddddd-dddd-4ddd-8ddd-dddddddddddd" }), true);
  assert.equal(threadHasUnsentContent({ ...thread("attached", 10), attachments: [{ uploadToken: "20000000-0000-4000-8000-000000000000", name: "a.pdf", mimeType: "application/pdf", sizeBytes: 1 }] }), true);
  assert.equal(threadHasUnsentContent({ ...thread("report", 10), inspectionFiles: { inspectionReport: { uploadToken: "30000000-0000-4000-8000-000000000000", name: "r.pdf", kind: "inspectionReport", mimeType: "application/pdf", sizeBytes: 1 } } }), true);
});

test("chatThreadFromSession maps the wire contract onto a thread", () => {
  const mapped = chatThreadFromSession(session("44444444-4444-4444-8444-444444444444", {
    workflowType: "codeRepair", stage: "planning", title: "Code repair task",
  }));
  assert.equal(mapped.id, "chat-44444444-4444-4444-8444-444444444444");
  assert.equal(mapped.workflowType, "codeRepair");
  assert.equal(mapped.stage, "planning");
  assert.equal(mapped.source, "local");
  assert.deepEqual(mapped.messages, []);
});

test("session refresh merges into bound threads without discarding local state", () => {
  const bound: ChatThread = {
    ...thread("local-1", 30),
    sessionId: "44444444-4444-4444-8444-444444444444",
    title: "Pump 4 seal review",
    draft: "Unsent follow-up",
    attachments: [{ uploadToken: "40000000-0000-4000-8000-000000000000", name: "photo.png", mimeType: "image/png", sizeBytes: 9 }],
    messages: [message("Earlier message")],
    messagesState: "ready",
    sendState: "sending",
    stage: "collectingInputs",
  };
  const pristine = thread("pristine", 25);
  const state = stateOf([bound, pristine], bound.id, "loading");
  const result = chatThreadReducer(state, {
    type: "sessionsLoaded", freshThreadId: "fresh" as ChatThreadId, now: 40,
    sessions: [session("44444444-4444-4444-8444-444444444444", { stage: "retrieving" })],
  });
  assert.equal(result.threads.length, 1);
  const merged = result.threads[0]!;
  assert.equal(merged.id, "local-1");
  assert.equal(merged.draft, "Unsent follow-up");
  assert.equal(merged.attachments.length, 1);
  assert.equal(merged.messages.length, 1);
  assert.equal(merged.messagesState, "ready");
  assert.equal(merged.sendState, "sending");
  assert.equal(merged.stage, "retrieving");
  assert.equal(merged.status, "active");
  assert.equal(merged.title, "Pump 4 seal review");
  assert.equal(merged.updatedAt, Date.parse("2026-09-01T10:05:00Z"));
  assert.equal(merged.seenInSessions, true);
  assert.equal(result.activeThreadId, "local-1");
});

test("session refresh adopts the backend title only for an unrenamed new chat", () => {
  const unnamed = { ...thread("local-1", 30), title: "New chat", sessionId: "44444444-4444-4444-8444-444444444444" };
  const state = stateOf([unnamed], unnamed.id, "loading");
  const result = chatThreadReducer(state, {
    type: "sessionsLoaded", freshThreadId: "fresh" as ChatThreadId, now: 40,
    sessions: [session("44444444-4444-4444-8444-444444444444", { title: "Backend title" })],
  });
  assert.equal(result.threads[0]?.title, "Backend title");
});

test("session refresh keeps bound threads with live send or unsent state", () => {
  const sending = {
    ...thread("sending", 30),
    title: "Pump 4 seal review",
    sessionId: "66666666-6666-4666-8666-666666666666",
    sendState: "sending" as const,
    messages: [message("Earlier message")],
  };
  const drafted = { ...thread("drafted", 25), sessionId: "77777777-7777-4777-8777-777777777777", draft: "Retry text" };
  const stale = { ...thread("stale", 20), sessionId: "88888888-8888-4888-8888-888888888888", seenInSessions: true };
  const state = stateOf([sending, drafted, stale], sending.id, "loading");
  const result = chatThreadReducer(state, {
    type: "sessionsLoaded", freshThreadId: "fresh" as ChatThreadId, now: 40,
    sessions: [session("44444444-4444-4444-8444-444444444444")],
  });
  assert.equal(result.threads.length, 3);
  assert.ok(result.threads.some((candidate) => candidate.id === sending.id));
  assert.ok(result.threads.some((candidate) => candidate.id === drafted.id));
  assert.ok(!result.threads.some((candidate) => candidate.id === stale.id));
  assert.equal(result.activeThreadId, sending.id);
});

test("a stale session list keeps a bound thread that was never listed", () => {
  // Reproduces the review race: the list was captured before the first bind,
  // and the append already completed, so the thread is idle with no draft.
  const bound: ChatThread = {
    ...thread("local-1", 30),
    title: "Pump 4 seal review",
    sessionId: "66666666-6666-4666-8666-666666666666",
    messages: [message("First stored message")],
    messagesState: "ready",
  };
  const state = stateOf([bound], bound.id, "loading");
  const loaded = chatThreadReducer(state, {
    type: "sessionsLoaded", freshThreadId: "fresh" as ChatThreadId, now: 40,
    sessions: [session("44444444-4444-4444-8444-444444444444")],
  });
  const kept = loaded.threads.find((candidate) => candidate.id === bound.id);
  assert.ok(kept);
  assert.equal(kept.messages.length, 1);
  assert.equal(loaded.activeThreadId, bound.id);

  // The pending stage sync must still reach the surviving thread.
  const synced = chatThreadReducer(loaded, {
    type: "sessionSynced", threadId: bound.id,
    session: session("66666666-6666-4666-8666-666666666666", { stage: "extracting", updatedAt: "2026-09-01T10:08:00Z" }),
  });
  assert.equal(synced.threads.find((candidate) => candidate.id === bound.id)?.stage, "extracting");
});

test("a listed bound thread absent from a later refresh leaves the list", () => {
  const removed = { ...thread("removed", 30), sessionId: "55555555-5555-4555-8555-555555555555", seenInSessions: true };
  const state = stateOf([removed], removed.id, "loading");
  const result = chatThreadReducer(state, {
    type: "sessionsLoaded", freshThreadId: "fresh" as ChatThreadId, now: 40,
    sessions: [session("44444444-4444-4444-8444-444444444444")],
  });
  assert.equal(result.threads.length, 1);
  assert.equal(result.threads[0]?.sessionId, "44444444-4444-4444-8444-444444444444");
});

test("a listed bound thread with unsent content survives a refresh that omits it", () => {
  const drafted = {
    ...thread("drafted", 30),
    sessionId: "55555555-5555-4555-8555-555555555555",
    seenInSessions: true,
    draft: "Unsent note",
  };
  const state = stateOf([drafted], drafted.id, "loading");
  const result = chatThreadReducer(state, {
    type: "sessionsLoaded", freshThreadId: "fresh" as ChatThreadId, now: 40,
    sessions: [session("44444444-4444-4444-8444-444444444444")],
  });
  assert.ok(result.threads.some((candidate) => candidate.id === drafted.id));
});

test("session refresh drops bound threads the backend no longer returns", () => {
  const stale = { ...thread("stale", 30), sessionId: "55555555-5555-4555-8555-555555555555", seenInSessions: true };
  const state = stateOf([stale], stale.id, "loading");
  const result = chatThreadReducer(state, {
    type: "sessionsLoaded", freshThreadId: "fresh" as ChatThreadId, now: 40,
    sessions: [session("44444444-4444-4444-8444-444444444444")],
  });
  assert.equal(result.threads.length, 1);
  assert.equal(result.threads[0]?.sessionId, "44444444-4444-4444-8444-444444444444");
  assert.equal(result.activeThreadId, "chat-44444444-4444-4444-8444-444444444444");
});

test("ambiguous appends resolve only by their own idempotency key", () => {
  const stored = [
    message("Pump 4 seal shows scoring"),
    { ...message("Second note"), clientMessageId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" },
  ];
  assert.equal(findDeliveredMessage(stored, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), stored[1]);
  assert.equal(findDeliveredMessage(stored, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"), undefined);
  // Legacy messages stored without a key can never satisfy a keyed lookup.
  const legacy = { ...stored[0]!, clientMessageId: null };
  assert.equal(findDeliveredMessage([legacy], "99999999-9999-4999-8999-999999999999"), undefined);
  // An identical text under a different key is a different append.
  assert.equal(
    findDeliveredMessage([{ ...stored[0]!, content: "Pump 4 seal shows scoring", clientMessageId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb" }], "99999999-9999-4999-8999-999999999999"),
    undefined,
  );
});

test("the pending append key survives failures and refreshes until resolution", () => {
  const first = { ...thread("first", 30), draft: "Ambiguous send" };
  const state = stateOf([first], first.id);
  const key = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
  const sessionKey = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";

  const started = chatThreadReducer(state, { type: "sendStarted", threadId: first.id, clientMessageId: key, clientSessionId: sessionKey, draft: "Ambiguous send" });
  assert.equal(started.threads[0]?.pendingClientMessageId, key);
  assert.equal(started.threads[0]?.pendingClientSessionId, sessionKey);
  assert.equal(started.threads[0]?.pendingDraft, "Ambiguous send");

  const ambiguous = chatThreadReducer(started, { type: "sendFailed", threadId: first.id, message: "The local service timed out. The message was not sent.", definitive: false });
  assert.equal(ambiguous.threads[0]?.pendingClientMessageId, key);
  assert.equal(ambiguous.threads[0]?.pendingClientSessionId, sessionKey);
  assert.equal(ambiguous.threads[0]?.pendingDraft, "Ambiguous send");
  assert.equal(ambiguous.threads[0]?.sendState, "error");

  const refreshed = chatThreadReducer(ambiguous, {
    type: "sessionsLoaded", freshThreadId: "fresh" as ChatThreadId, now: 35,
    sessions: [session("44444444-4444-4444-8444-444444444444")],
  });
  // This thread is not yet bound, so it survives only through its draft.
  assert.ok(refreshed.threads.some((candidate) => candidate.draft === "Ambiguous send"));

  const bound = {
    ...thread("bound", 40),
    sessionId: "55555555-5555-4555-8555-555555555555",
    draft: "Ambiguous send",
  };
  const boundState = stateOf([bound], bound.id);
  const boundStarted = chatThreadReducer(boundState, { type: "sendStarted", threadId: bound.id, clientMessageId: key, draft: "Ambiguous send" });
  const boundRefreshed = chatThreadReducer(boundStarted, {
    type: "sessionsLoaded", freshThreadId: "fresh" as ChatThreadId, now: 45,
    sessions: [session("55555555-5555-4555-8555-555555555555")],
  });
  assert.equal(boundRefreshed.threads[0]?.pendingClientMessageId, key);

  const delivered = chatThreadReducer(boundRefreshed, { type: "sendResolved", threadId: bound.id, now: 50 });
  assert.equal(delivered.threads[0]?.pendingClientMessageId, undefined);
  assert.equal(delivered.threads[0]?.pendingDraft, undefined);
  assert.equal(delivered.threads[0]?.sendState, "idle");

  const definitive = chatThreadReducer(boundStarted, { type: "sendFailed", threadId: bound.id, message: "The message was rejected.", definitive: true });
  assert.equal(definitive.threads[0]?.pendingClientMessageId, undefined);
  assert.equal(definitive.threads[0]?.pendingClientSessionId, undefined);
  assert.equal(definitive.threads[0]?.pendingDraft, undefined);
  assert.equal(definitive.threads[0]?.sendState, "error");

  const appended = chatThreadReducer(boundStarted, {
    type: "messageAppended", threadId: bound.id,
    message: { ...message("Ambiguous send"), clientMessageId: key },
    now: 55,
  });
  assert.equal(appended.threads[0]?.pendingClientMessageId, undefined);
  assert.equal(appended.threads[0]?.pendingClientSessionId, undefined);
  assert.equal(appended.threads[0]?.pendingDraft, undefined);
  assert.equal(appended.threads[0]?.messages.length, 1);
});

test("a replayed session create binds the draft thread instead of duplicating it", () => {
  // The create committed on FastAPI but its response was lost: the thread
  // stays unbound with its draft while the next refresh lists the session.
  const orphaned = { ...thread("orphaned", 30), title: "New chat", draft: "First message", sendState: "error" as const };
  const state = stateOf([orphaned], orphaned.id, "loading");
  const started = chatThreadReducer(state, {
    type: "sendStarted", threadId: orphaned.id,
    clientMessageId: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    clientSessionId: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    draft: "First message",
  });
  const failed = chatThreadReducer(started, { type: "sendFailed", threadId: orphaned.id, message: "The local service timed out. The message was not sent.", definitive: false });

  const refreshed = chatThreadReducer(failed, {
    type: "sessionsLoaded", freshThreadId: "fresh" as ChatThreadId, now: 40,
    sessions: [session("55555555-5555-4555-8555-555555555555", { clientSessionId: "dddddddd-dddd-4ddd-8ddd-dddddddddddd", title: "First message" })],
  });
  // The draft thread rebinds to the committed session; no duplicate thread
  // is created for the same session.
  assert.equal(refreshed.threads.length, 1);
  const bound = refreshed.threads[0]!;
  assert.equal(bound.id, orphaned.id);
  assert.equal(bound.sessionId, "55555555-5555-4555-8555-555555555555");
  assert.equal(bound.title, "First message");
  assert.equal(bound.draft, "First message");
  assert.equal(bound.pendingClientSessionId, undefined);
  assert.equal(bound.pendingClientMessageId, "cccccccc-cccc-4ccc-8ccc-cccccccccccc");
  assert.equal(bound.seenInSessions, true);
  assert.equal(refreshed.activeThreadId, orphaned.id);

  // A retry create of the same key also clears the pending session key.
  const rebound = chatThreadReducer(refreshed, {
    type: "sessionBound", threadId: orphaned.id,
    session: session("55555555-5555-4555-8555-555555555555"),
  });
  assert.equal(rebound.threads[0]?.pendingClientSessionId, undefined);
});

test("an unbound thread with a pending session key survives a refresh that omits it", () => {
  const orphaned = {
    ...thread("orphaned", 30),
    draft: "",
    sendState: "error" as const,
    pendingClientSessionId: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
  };
  const state = stateOf([orphaned], orphaned.id, "loading");
  const refreshed = chatThreadReducer(state, {
    type: "sessionsLoaded", freshThreadId: "fresh" as ChatThreadId, now: 40,
    sessions: [],
  });
  // The committed session is not listed yet, but the unresolved send means
  // the thread may still bind to it on a later refresh.
  assert.ok(refreshed.threads.some((candidate) => candidate.id === orphaned.id));
});

test("a retry resends the pending draft snapshot and keeps later edits", () => {
  const first = { ...thread("first", 30), draft: "Ambiguous send" };
  const state = stateOf([first], first.id);
  const key = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";

  const started = chatThreadReducer(state, { type: "sendStarted", threadId: first.id, clientMessageId: key, draft: "Ambiguous send" });
  const ambiguous = chatThreadReducer(started, { type: "sendFailed", threadId: first.id, message: "The local service timed out. The message was not sent.", definitive: false });
  const edited = chatThreadReducer(ambiguous, { type: "updateDraft", threadId: first.id, draft: "Ambiguous send, edited", now: 35 });

  // A retry re-enters sendStarted, but the snapshot stays bound to the key.
  const retried = chatThreadReducer(edited, { type: "sendStarted", threadId: first.id, clientMessageId: key, draft: "Ambiguous send, edited" });
  assert.equal(retried.threads[0]?.pendingClientMessageId, key);
  assert.equal(retried.threads[0]?.pendingDraft, "Ambiguous send");
  assert.equal(retried.threads[0]?.sendState, "sending");

  // Resolving the retried append clears only the submitted snapshot.
  const delivered = chatThreadReducer(retried, {
    type: "messageAppended", threadId: first.id,
    message: { ...message("Ambiguous send"), clientMessageId: key },
    now: 40,
  });
  const cleared = chatThreadReducer(delivered, { type: "draftClearedIfUnchanged", threadId: first.id, draft: "Ambiguous send", now: 45 });
  assert.equal(cleared.threads[0]?.draft, "Ambiguous send, edited");
  assert.equal(cleared.threads[0]?.pendingDraft, undefined);
  assert.equal(cleared.threads[0]?.pendingClientMessageId, undefined);

  // The unresolved snapshot alone keeps the thread across a refresh.
  const unresolved = { ...ambiguous.threads[0]!, draft: "" };
  assert.equal(threadHasUnsentContent(unresolved), true);
});

test("send resolution clears an ambiguous failure exactly once", () => {
  const first = { ...thread("first", 30), draft: "Ambiguous send", sendState: "sending" as const, pendingClientMessageId: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee" };
  const state = stateOf([first], first.id);
  const failed = chatThreadReducer(state, { type: "sendFailed", threadId: first.id, message: "The local service timed out. The message was not sent.", definitive: false });
  assert.equal(failed.threads[0]?.pendingClientMessageId, "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee");
  const resolved = chatThreadReducer(failed, { type: "sendResolved", threadId: first.id, now: 40 });
  assert.equal(resolved.threads[0]?.sendState, "idle");
  assert.equal(resolved.threads[0]?.sendError, undefined);
  assert.equal(resolved.threads[0]?.pendingClientMessageId, undefined);
  assert.equal(resolved.threads[0]?.draft, "Ambiguous send");
  assert.equal(chatThreadReducer(resolved, { type: "sendResolved", threadId: first.id, now: 50 }), resolved);
});

test("stage pipelines reflect active, failed, and terminal states", () => {
  const active = chatStageSteps("inspectionAnalysis", "retrieving", "active");
  assert.deepEqual(active.map((step) => step.state), ["done", "done", "active", "queued", "queued", "queued", "queued"]);
  assert.deepEqual(active.map((step) => step.stage), [
    "collectingInputs", "extracting", "retrieving", "drafting", "validating", "awaitingApproval", "exporting",
  ]);

  const failed = chatStageSteps("inspectionAnalysis", "extracting", "failed");
  assert.deepEqual(failed.map((step) => step.state), ["done", "failed", "queued", "queued", "queued", "queued", "queued"]);

  const codeRepair = chatStageSteps("codeRepair", "sandboxExecuting", "active");
  assert.deepEqual(codeRepair.map((step) => step.stage), [
    "collectingInputs", "planning", "awaitingApproval", "sandboxExecuting", "repairing",
  ]);
  assert.deepEqual(codeRepair.map((step) => step.state), ["done", "done", "done", "active", "queued"]);

  assert.deepEqual(chatStageSteps("inspectionAnalysis", "completed", "completed"), []);
  assert.deepEqual(chatStageSteps("inspectionAnalysis", "approvalRejected", "approvalRejected"), []);
  assert.deepEqual(chatStageSteps("inspectionAnalysis", "collectingInputs", "active"), [
    { stage: "collectingInputs", state: "active" },
    { stage: "extracting", state: "queued" },
    { stage: "retrieving", state: "queued" },
    { stage: "drafting", state: "queued" },
    { stage: "validating", state: "queued" },
    { stage: "awaitingApproval", state: "queued" },
    { stage: "exporting", state: "queued" },
  ]);
});

test("workflow events drive queued, processing, approval, and failure states without duplicates", () => {
  const bound = { ...thread("bound", 30), sessionId: "33333333-3333-4333-8333-333333333333", status: "active" as const, stage: "collectingInputs" as const };
  const baseEvent = {
    eventId: 1,
    sessionId: bound.sessionId,
    workflowRunId: "55555555-5555-4555-8555-555555555555",
    eventType: "message.accepted" as const,
    occurredAt: "2026-09-01T10:07:00Z",
    payload: { messageId: "11111111-1111-4111-8111-111111111111" },
  };
  const queued = chatThreadReducer(stateOf([bound], bound.id), { type: "workflowEvent", threadId: bound.id, event: baseEvent });
  assert.equal(queued.threads[0]?.workflowState, "queued");
  assert.equal(chatThreadReducer(queued, { type: "workflowEvent", threadId: bound.id, event: baseEvent }), queued);

  const processing = chatThreadReducer(queued, {
    type: "workflowEvent", threadId: bound.id,
    event: { ...baseEvent, eventId: 2, eventType: "workflow.stageChanged", payload: { previousStage: "collectingInputs", stage: "extracting", stageVersion: 1, status: "active" } },
  });
  assert.equal(processing.threads[0]?.workflowState, "processing");
  assert.equal(processing.threads[0]?.stage, "extracting");

  const approval = chatThreadReducer(processing, {
    type: "workflowEvent", threadId: bound.id,
    event: { ...baseEvent, eventId: 3, eventType: "approval.required", payload: { approvalId: "66666666-6666-4666-8666-666666666666" } },
  });
  assert.equal(approval.threads[0]?.workflowState, "awaitingApproval");

  const failed = chatThreadReducer(approval, {
    type: "workflowEvent", threadId: bound.id,
    event: { ...baseEvent, eventId: 4, eventType: "workflow.failed", payload: { stage: "validating", failureCode: "validation_failed" } },
  });
  assert.equal(failed.threads[0]?.workflowState, "failed");
  assert.equal(failed.threads[0]?.status, "failed");
  assert.equal(failed.threads[0]?.activityEvents.length, 4);
});

test("a reconnected activity stream clears its transient disconnect error", () => {
  const active = thread("active", 30);
  const failed = chatThreadReducer(stateOf([active], active.id), {
    type: "streamFailed",
    threadId: active.id,
    message: "Workflow activity disconnected. Reconnecting…",
  });
  assert.equal(failed.threads[0]?.streamError, "Workflow activity disconnected. Reconnecting…");

  const connected = chatThreadReducer(failed, { type: "streamConnected", threadId: active.id });
  assert.equal(connected.threads[0]?.streamError, undefined);
  assert.equal(chatThreadReducer(connected, { type: "streamConnected", threadId: active.id }), connected);
});
