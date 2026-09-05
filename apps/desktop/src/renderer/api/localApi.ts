import { LOCAL_API_ORIGIN } from "../../shared/contracts";
import type {
  EmployeeLoginRequest,
  EmployeeLoginResponse,
  EmployeeLogoutResponse,
  EmployeeSession,
  EmployeeSessionRestoreResponse,
  HealthResponse,
  OutboundStatus,
} from "../../shared/contracts";

const requestTimeoutMs = 5_000;

/**
 * These paths are the pending frontend expectation for Backend 1. No FastAPI
 * route is added by the desktop client. Keep the assumptions here until the
 * backend contract is agreed and verified.
 */
const localApiEndpoints = {
  health: "/health",
  employeeLogin: "/auth/login",
  employeeLogout: "/auth/logout",
  restoreEmployeeSession: "/auth/session",
} as const;

export type LocalApiErrorKind =
  | "invalidUrl"
  | "network"
  | "timeout"
  | "unauthorized"
  | "endpointUnavailable"
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

function getApiBaseUrl(): string {
  return LOCAL_API_ORIGIN;
}

function getApiBaseUrlFromValue(configuredUrl: string): string {
  let url: URL;
  try {
    url = new URL(configuredUrl);
  } catch {
    throw new LocalApiError("The local API URL is invalid.", "invalidUrl");
  }

  if (
    url.origin !== LOCAL_API_ORIGIN ||
    url.pathname !== "/" ||
    url.search !== "" ||
    url.hash !== "" ||
    url.username !== "" ||
    url.password !== ""
  ) {
    throw new LocalApiError("WorkBench only permits its configured FastAPI origin.", "invalidUrl");
  }
  return LOCAL_API_ORIGIN;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
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
  private async requestJson(path: string, init: RequestInit, apiBaseUrl: string, operation: string): Promise<unknown> {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), requestTimeoutMs);

    try {
      const response = await fetch(`${getApiBaseUrlFromValue(apiBaseUrl)}${path}`, {
        ...init,
        credentials: "include",
        headers: {
          Accept: "application/json",
          ...init.headers,
        },
        signal: controller.signal,
      });
      if (response.status === 401 || response.status === 403) {
        throw new LocalApiError(`The local employee ${operation} was not authorized.`, "unauthorized", response.status);
      }
      if (response.status === 404) {
        throw new LocalApiError(
          `The local employee ${operation} endpoint is unavailable on FastAPI.`,
          "endpointUnavailable",
          response.status,
        );
      }
      if (!response.ok) {
        throw new LocalApiError(`FastAPI ${operation} returned HTTP ${response.status}.`, "http", response.status);
      }
      let responseText: string;
      try {
        responseText = await response.text();
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          throw new LocalApiError(`FastAPI ${operation} timed out.`, "timeout");
        }
        throw new LocalApiError(`FastAPI ${operation} response could not be read.`, "network");
      }
      try {
        return JSON.parse(responseText) as unknown;
      } catch {
        throw new LocalApiError(`FastAPI returned malformed JSON for ${operation}.`, "malformedJson");
      }
    } catch (error) {
      if (error instanceof LocalApiError) {
        throw error;
      }
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new LocalApiError(`FastAPI ${operation} timed out.`, "timeout");
      }
      throw new LocalApiError(`FastAPI is unavailable for local employee ${operation}.`, "network");
    } finally {
      window.clearTimeout(timeout);
    }
  }

  async login(request: EmployeeLoginRequest, apiBaseUrl = getApiBaseUrl()): Promise<EmployeeSession> {
    const response: EmployeeLoginResponse = {
      session: parseEmployeeSession(
        await this.requestJson(
          localApiEndpoints.employeeLogin,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(request),
          },
          apiBaseUrl,
          "employee login",
        ),
        "employee login",
      ),
    };
    return response.session;
  }

  async logout(apiBaseUrl = getApiBaseUrl()): Promise<EmployeeLogoutResponse> {
    return parseLogoutResponse(
      await this.requestJson(
        localApiEndpoints.employeeLogout,
        { method: "POST" },
        apiBaseUrl,
        "employee logout",
      ),
    );
  }

  async restoreSession(apiBaseUrl = getApiBaseUrl()): Promise<EmployeeSession> {
    const response: EmployeeSessionRestoreResponse = {
      session: parseEmployeeSession(
        await this.requestJson(
          localApiEndpoints.restoreEmployeeSession,
          { method: "GET" },
          apiBaseUrl,
          "session restoration",
        ),
        "session restoration",
      ),
    };
    return response.session;
  }

  async getHealth(apiBaseUrl = getApiBaseUrl()): Promise<HealthResponse> {
    const value = await this.requestJson(localApiEndpoints.health, { method: "GET" }, apiBaseUrl, "health check");
    return parseHealthResponse(value);
  }
}

export const localApi = new LocalApiClient();
