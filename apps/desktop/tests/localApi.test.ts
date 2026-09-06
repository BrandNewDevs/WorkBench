import assert from "node:assert/strict";
import { test } from "node:test";
import type { ChatMessage, ChatSession, LocalServiceRequest, LocalServiceResponse } from "../src/shared/contracts.ts";
import { LocalApiError, apiFailureWasDefinitive, localApi } from "../src/renderer/api/localApi.ts";

interface StubBridge {
  requestLocalService(request: LocalServiceRequest): Promise<LocalServiceResponse>;
}

function installBridge(stub: StubBridge): void {
  (globalThis as { window?: unknown }).window = {
    setTimeout: (callback: () => void, ms: number) => setTimeout(callback, ms),
    clearTimeout: (timer: ReturnType<typeof setTimeout>) => clearTimeout(timer),
    workbench: stub,
  };
}

function ok(body: unknown): Promise<LocalServiceResponse> {
  return Promise.resolve({ status: 200, body: JSON.stringify(body) });
}

const sessionPayload = {
  sessionId: "1ef46b0e-7c1a-4d9e-9f2a-3f5c6b7d8e9f",
  ownerUserId: "0ef46b0e-7c1a-4d9e-9f2a-3f5c6b7d8e90",
  workflowType: "inspectionAnalysis",
  title: "Inspection review",
  stage: "collectingInputs",
  status: "active",
  createdAt: "2026-09-06T01:20:00Z",
  updatedAt: "2026-09-06T01:21:00Z",
  clientSessionId: null,
};

const messagePayload = {
  messageId: "2ef46b0e-7c1a-4d9e-9f2a-3f5c6b7d8e91",
  sessionId: sessionPayload.sessionId,
  authorUserId: sessionPayload.ownerUserId,
  role: "user",
  content: "Find the corrosion findings.",
  createdAt: "2026-09-06T01:21:00Z",
  clientMessageId: "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
};

test("chat session and message responses parse into strict camelCase contracts", async () => {
  installBridge({
    requestLocalService: async (request) => {
      assert.equal(request.operation, "chatListSessions");
      return ok({ sessions: [sessionPayload] });
    },
  });

  const listing = await localApi.listChatSessions();
  assert.equal(listing.sessions.length, 1);
  const session: ChatSession = listing.sessions[0] as ChatSession;
  assert.equal(session.stage, "collectingInputs");
  assert.equal(session.status, "active");
  assert.equal(session.workflowType, "inspectionAnalysis");

  installBridge({
    requestLocalService: async (request) => {
      assert.equal(request.operation, "chatListMessages");
      assert.ok("sessionId" in request);
      return ok({ messages: [messagePayload, { ...messagePayload, role: "assistant", authorUserId: null, clientMessageId: null }] });
    },
  });

  const messages = await localApi.listChatMessages(sessionPayload.sessionId);
  const user: ChatMessage = messages.messages[0] as ChatMessage;
  const assistant: ChatMessage = messages.messages[1] as ChatMessage;
  assert.equal(user.role, "user");
  assert.equal(assistant.role, "assistant");
  assert.equal(assistant.authorUserId, null);
});

test("malformed chat payloads are rejected instead of trusted", async () => {
  const invalidSessionLists: unknown[] = [
    { sessions: [{ ...sessionPayload, stage: "teleporting" }] },
    { sessions: [{ ...sessionPayload, status: "queued" }] },
    { sessions: [{ ...sessionPayload, workflowType: "cloudGpt" }] },
    { sessions: [{ ...sessionPayload, createdAt: "not-a-date" }] },
    { sessions: {} },
    {},
  ];
  for (const payload of invalidSessionLists) {
    installBridge({ requestLocalService: async () => ok(payload) });
    await assert.rejects(
      localApi.listChatSessions(),
      (error: unknown) => error instanceof Error && error.name === "LocalApiError",
    );
  }

  const invalidMessageLists: unknown[] = [
    { messages: [{ ...messagePayload, role: "system" }] },
    { messages: [{ ...messagePayload, content: "" }] },
    { messages: [{ ...messagePayload, authorUserId: 42 }] },
    { messages: [{ ...messagePayload, createdAt: "2026-09-06T01:21:00" }] },
    { messages: [{ ...messagePayload, clientMessageId: "not-a-uuid" }] },
    { messages: [{ ...messagePayload, reasoning: "secret chain of thought" }] },
    { messages: null },
  ];
  for (const payload of invalidMessageLists) {
    installBridge({ requestLocalService: async () => ok(payload) });
    await assert.rejects(
      localApi.listChatMessages(sessionPayload.sessionId),
      (error: unknown) => error instanceof Error && error.name === "LocalApiError",
    );
  }
});

test("chat resource 404s are distinguished from missing endpoints", async () => {
  installBridge({
    requestLocalService: async () => ({
      status: 404,
      body: JSON.stringify({ code: "session_not_found", message: "The chat session was not found for this employee." }),
    }),
  });
  await assert.rejects(
    localApi.listChatMessages(sessionPayload.sessionId),
    (error: unknown) => error instanceof LocalApiError && error.kind === "resourceNotFound" && error.status === 404,
  );

  installBridge({
    requestLocalService: async () => ({ status: 404, body: "Not Found" }),
  });
  await assert.rejects(
    localApi.listChatMessages(sessionPayload.sessionId),
    (error: unknown) => error instanceof LocalApiError && error.kind === "endpointUnavailable",
  );
});

test("only answered service failures may release an idempotency key", () => {
  // FastAPI answered and refused before any write: nothing was stored.
  assert.equal(apiFailureWasDefinitive(new LocalApiError("rejected", "unauthorized", 401)), true);
  assert.equal(apiFailureWasDefinitive(new LocalApiError("closed session", "http", 409)), true);
  assert.equal(apiFailureWasDefinitive(new LocalApiError("missing session", "resourceNotFound", 404)), true);
  assert.equal(apiFailureWasDefinitive(new LocalApiError("missing route", "endpointUnavailable", 404)), true);
  // A 5xx proves nothing: the service can fail after the same append
  // committed through a concurrent request, so the key must stay.
  assert.equal(apiFailureWasDefinitive(new LocalApiError("service error", "http", 500)), false);
  assert.equal(apiFailureWasDefinitive(new LocalApiError("overloaded", "http", 503)), false);
  // The request may still be in flight; absence from a read proves nothing.
  assert.equal(apiFailureWasDefinitive(new LocalApiError("timed out", "timeout")), false);
  assert.equal(apiFailureWasDefinitive(new LocalApiError("pipe closed", "network")), false);
  assert.equal(apiFailureWasDefinitive(new LocalApiError("unreadable body", "malformedJson")), false);
  assert.equal(apiFailureWasDefinitive(new LocalApiError("unexpected body", "invalidResponse")), false);
  assert.equal(apiFailureWasDefinitive(new Error("unrelated failure")), false);
});

test("create and append round-trip the request bodies to the local service", async () => {
  const requests: LocalServiceRequest[] = [];
  installBridge({
    requestLocalService: async (request) => {
      requests.push(request);
      if (request.operation === "chatCreateSession") {
        return ok(sessionPayload);
      }
      return ok(messagePayload);
    },
  });

  const created = await localApi.createChatSession({
    workflowType: "inspectionAnalysis",
    title: "Inspection review",
    clientSessionId: "4ef46b0e-7c1a-4d9e-9f2a-3f5c6b7d8e92",
  });
  const appended = await localApi.appendChatMessage(sessionPayload.sessionId, {
    content: "Find the corrosion findings.",
    clientMessageId: "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d",
  });
  assert.equal(created.sessionId, sessionPayload.sessionId);
  assert.equal(appended.messageId, messagePayload.messageId);
  assert.deepEqual(requests[0], {
    operation: "chatCreateSession",
    request: {
      workflowType: "inspectionAnalysis",
      title: "Inspection review",
      clientSessionId: "4ef46b0e-7c1a-4d9e-9f2a-3f5c6b7d8e92",
    },
  });
  assert.deepEqual(requests[1], {
    operation: "chatAppendMessage",
    sessionId: sessionPayload.sessionId,
    request: { content: "Find the corrosion findings.", clientMessageId: "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d" },
  });
});
