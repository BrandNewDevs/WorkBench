import type {
  ChatMessage,
  ChatMessageAppendRequest,
  ChatMessageListResponse,
  ChatSession,
  ChatSessionCreateRequest,
  ChatSessionListResponse,
  EmployeeLoginRequest,
  EmployeeLoginResponse,
  EmployeeLogoutResponse,
  EmployeeSession,
  EmployeeSessionRestoreResponse,
  HealthResponse,
  OutboundStatus,
  LocalServiceRequest,
} from "../../shared/contracts";
import {
  chatErrorCodeSchema,
  chatMessageListResponseSchema,
  chatMessageSchema,
  chatSessionListResponseSchema,
  chatSessionSchema,
} from "../../shared/contracts.ts";
import type { ZodType } from "zod";

const requestTimeoutMs = 5_000;

export type LocalApiErrorKind =
  | "invalidUrl"
  | "network"
  | "timeout"
  | "unauthorized"
  | "endpointUnavailable"
  | "resourceNotFound"
  | "http"
  | "malformedJson"
  | "expiredSession"
  | "invalidResponse";

export class LocalApiError extends Error {
  readonly kind: LocalApiErrorKind;
  readonly status: number | undefined;

  constructor(message: string, kind: LocalApiErrorKind, status?: number) {
    super(message);
    this.name = "LocalApiError";
    this.kind = kind;
    this.status = status;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseErrorCode(body: string): string | undefined {
  try {
    const parsed = JSON.parse(body) as unknown;
    return isRecord(parsed) && typeof parsed.code === "string" ? parsed.code : undefined;
  } catch {
    return undefined;
  }
}

function parseChat<T>(schema: ZodType<T>, value: unknown, operation: string): T {
  const result = schema.safeParse(value);
  if (!result.success) {
    throw new LocalApiError(`FastAPI returned an invalid ${operation} response.`, "invalidResponse");
  }
  return result.data;
}

function parseEmployeeSession(value: unknown, responseName: string): EmployeeSession {
  if (!isRecord(value) || !isRecord(value.session)) {
    throw new LocalApiError(`FastAPI returned an invalid ${responseName} response.`, "invalidResponse");
  }

  const session = value.session;
  const user = session.user;
  if (
    !isRecord(user) ||
    typeof session.sessionId !== "string" ||
    session.sessionId.length === 0 ||
    typeof session.expiresAt !== "string" ||
    !Number.isFinite(Date.parse(session.expiresAt)) ||
    typeof user.employeeId !== "string" ||
    user.employeeId.length === 0 ||
    typeof user.username !== "string" ||
    user.username.length === 0 ||
    typeof user.displayName !== "string" ||
    user.displayName.length === 0 ||
    user.role !== "employee"
  ) {
    throw new LocalApiError(`FastAPI returned an invalid ${responseName} response.`, "invalidResponse");
  }

  if (Date.parse(session.expiresAt) <= Date.now()) {
    throw new LocalApiError(`FastAPI returned an expired ${responseName} session.`, "expiredSession");
  }

  return {
    sessionId: session.sessionId,
    user: {
      employeeId: user.employeeId,
      username: user.username,
      displayName: user.displayName,
      role: "employee",
    },
    expiresAt: session.expiresAt,
  };
}

function parseLogoutResponse(value: unknown): EmployeeLogoutResponse {
  if (!isRecord(value) || typeof value.revoked !== "boolean") {
    throw new LocalApiError("FastAPI returned an invalid employee logout response.", "invalidResponse");
  }
  return { revoked: value.revoked };
}

function parseHealthResponse(value: unknown): HealthResponse {
  if (!isRecord(value)) {
    throw new LocalApiError("FastAPI returned an invalid health response.", "invalidResponse");
  }

  const rawStatus = value.status;
  const status = rawStatus === "ok" || rawStatus === "healthy" ? "healthy" : rawStatus === "degraded" ? "degraded" : undefined;
  const outboundStatus = value.outboundStatus;
  const validOutboundStatus: OutboundStatus | undefined =
    outboundStatus === "blocked" || outboundStatus === "clear" || outboundStatus === "unknown"
      ? outboundStatus
      : undefined;
  const externalApis = value.externalApis;

  if (
    !status ||
    value.service !== "fastapi" ||
    typeof value.localInference !== "boolean" ||
    (typeof value.currentModel !== "string" && value.currentModel !== null) ||
    typeof externalApis !== "number" ||
    !Number.isInteger(externalApis) ||
    externalApis < 0 ||
    !validOutboundStatus ||
    typeof value.checkedAt !== "string" ||
    !Number.isFinite(Date.parse(value.checkedAt))
  ) {
    throw new LocalApiError("FastAPI returned an invalid health response.", "invalidResponse");
  }

  return {
    status,
    service: "fastapi",
    localInference: value.localInference,
    currentModel: value.currentModel,
    externalApis,
    outboundStatus: validOutboundStatus,
    checkedAt: value.checkedAt,
  };
}

export class LocalApiClient {
  private async requestJson(request: LocalServiceRequest, operation: string): Promise<unknown> {
    let timeout: number | undefined;
    try {
      const pending = window.workbench.requestLocalService(request);
      const response = await Promise.race([
        pending,
        new Promise<never>((_, reject) => {
          timeout = window.setTimeout(() => reject(new LocalApiError(`FastAPI ${operation} timed out.`, "timeout")), requestTimeoutMs);
        }),
      ]);
      if (response.status === 401 || response.status === 403) throw new LocalApiError(`The local employee ${operation} was not authorized.`, "unauthorized", response.status);
      if (response.status === 404) {
        const errorCode = chatErrorCodeSchema.safeParse(parseErrorCode(response.body));
        if (errorCode.success && errorCode.data === "session_not_found") {
          throw new LocalApiError("The chat session was not found for this employee.", "resourceNotFound", response.status);
        }
        throw new LocalApiError(`The local employee ${operation} endpoint is unavailable on FastAPI.`, "endpointUnavailable", response.status);
      }
      if (response.status < 200 || response.status >= 300) throw new LocalApiError(`FastAPI ${operation} returned HTTP ${response.status}.`, "http", response.status);
      try { return JSON.parse(response.body) as unknown; } catch { throw new LocalApiError(`FastAPI returned malformed JSON for ${operation}.`, "malformedJson"); }
    } catch (error) {
      if (error instanceof LocalApiError) throw error;
      throw new LocalApiError(`FastAPI is unavailable for local employee ${operation}.`, "network");
    } finally {
      if (timeout !== undefined) window.clearTimeout(timeout);
    }
  }

  async login(request: EmployeeLoginRequest, apiBaseUrl?: string): Promise<EmployeeSession> {
    void apiBaseUrl;
    const response: EmployeeLoginResponse = { session: parseEmployeeSession(await this.requestJson({ operation: "login", request }, "employee login"), "employee login") };
    return response.session;
  }

  async logout(apiBaseUrl?: string): Promise<EmployeeLogoutResponse> {
    void apiBaseUrl;
    return parseLogoutResponse(await this.requestJson({ operation: "logout" }, "employee logout"));
  }

  async restoreSession(apiBaseUrl?: string): Promise<EmployeeSession> {
    void apiBaseUrl;
    const response: EmployeeSessionRestoreResponse = { session: parseEmployeeSession(await this.requestJson({ operation: "restoreSession" }, "session restoration"), "session restoration") };
    return response.session;
  }

  async getHealth(apiBaseUrl?: string): Promise<HealthResponse> {
    void apiBaseUrl;
    return parseHealthResponse(await this.requestJson({ operation: "health" }, "health check"));
  }

  async listChatSessions(apiBaseUrl?: string): Promise<ChatSessionListResponse> {
    void apiBaseUrl;
    return parseChat(
      chatSessionListResponseSchema,
      await this.requestJson({ operation: "chatListSessions" }, "chat session listing"),
      "chat session listing",
    );
  }

  async createChatSession(request: ChatSessionCreateRequest, apiBaseUrl?: string): Promise<ChatSession> {
    void apiBaseUrl;
    return parseChat(
      chatSessionSchema,
      await this.requestJson({ operation: "chatCreateSession", request }, "chat session creation"),
      "chat session",
    );
  }

  async getChatSession(sessionId: string, apiBaseUrl?: string): Promise<ChatSession> {
    void apiBaseUrl;
    return parseChat(
      chatSessionSchema,
      await this.requestJson({ operation: "chatGetSession", sessionId }, "chat session detail"),
      "chat session",
    );
  }

  async listChatMessages(sessionId: string, apiBaseUrl?: string): Promise<ChatMessageListResponse> {
    void apiBaseUrl;
    return parseChat(
      chatMessageListResponseSchema,
      await this.requestJson({ operation: "chatListMessages", sessionId }, "chat message listing"),
      "chat message listing",
    );
  }

  async appendChatMessage(
    sessionId: string,
    request: ChatMessageAppendRequest,
    apiBaseUrl?: string,
  ): Promise<ChatMessage> {
    void apiBaseUrl;
    return parseChat(
      chatMessageSchema,
      await this.requestJson({ operation: "chatAppendMessage", sessionId, request }, "chat message"),
      "chat message",
    );
  }
}

export const localApi = new LocalApiClient();
