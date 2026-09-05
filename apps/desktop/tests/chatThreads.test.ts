import assert from "node:assert/strict";
import { test } from "node:test";
import { chatThreadReducer, type ChatThread, type ChatThreadId } from "../src/renderer/lib/chatThreads.ts";

function thread(id: string, updatedAt: number, createdAt = 0): ChatThread {
  return {
    id: id as ChatThreadId, title: id, source: "local", draft: "", attachments: [],
    inspectionFiles: {}, createdAt, updatedAt,
  };
}

test("edits preserve ordering, other drafts, and the selected chat", () => {
  const first = thread("first", 30);
  const second = thread("second", 20);
  const third = thread("third", 10);
  const state = { threads: [first, second, third], activeThreadId: first.id };
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
  const state = { threads: [first, second, third], activeThreadId: first.id };
  const rolledBack = chatThreadReducer(state, { type: "updateDraft", threadId: first.id, draft: "Rollback", now: 10 });
  assert.deepEqual(rolledBack.threads.map(({ id }) => id), [second.id, third.id, first.id]);
  const tied = chatThreadReducer(state, { type: "updateDraft", threadId: third.id, draft: "Tie", now: 20 });
  assert.deepEqual(tied.threads.map(({ id }) => id), [first.id, second.id, third.id]);
});

test("new chat reuses an empty draft without replacing another chat's files", () => {
  const first = thread("first", 30);
  const state = { threads: [first], activeThreadId: first.id };
  const created = chatThreadReducer(state, { type: "create", threadId: "new" as ChatThreadId, now: 40 });
  const reused = chatThreadReducer(created, { type: "create", threadId: "unused" as ChatThreadId, now: 50 });
  assert.equal(reused.threads.length, 2);
  assert.equal(reused.activeThreadId, "new");
  const withFile = chatThreadReducer(reused, {
    type: "setInspectionFile", threadId: first.id, kind: "inspectionReport", now: 60,
    file: { name: "report.pdf", kind: "inspectionReport", mimeType: "application/pdf", sizeBytes: 42 },
  });
  assert.equal(withFile.threads[0]?.inspectionFiles.inspectionReport?.name, "report.pdf");
  assert.deepEqual(withFile.threads[1]?.inspectionFiles, {});
});
