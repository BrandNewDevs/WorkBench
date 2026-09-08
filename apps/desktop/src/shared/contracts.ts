import { z } from "zod";
import type { TurnStreamRequest, TurnStreamEvent } from "./pdf";

export const IPC_CHANNELS = {
  getDesktopStatus: "desktop:get-status",
  selectUploadFiles: "desktop:select-upload-files",
  selectChatAttachments: "desktop:select-chat-attachments",
  requestLocalService: "desktop:request-local-service",
  startSessionEvents: "desktop:start-session-events",
  stopSessionEvents: "desktop:stop-session-events",
  sessionEvent: "desktop:session-event",
  startTurn: "desktop:start-turn",
  turnEvent: "desktop:turn-event",
} as const;

/** The only FastAPI origin the desktop client may contact. */
export const LOCAL_API_ORIGIN = "http://127.0.0.1:8000";

/**
 * Generation-specific IPC watchdogs. Local text generation can legitimately
 * run for minutes on Jetson-class hardware, while health, auth, and session
 * traffic keep the short general timeout.
 */
export const minGenerationRequestTimeoutMs = 120_000;
/** Watchdog the Electron main process applies to generation requests. */
export const localGenerationRequestTimeoutMs = 180_000;
/**
 * The renderer race must outlive the main-process watchdog so a slow local
 * reply surfaces as an error instead of being silently dropped.
 */
export const rendererGenerationRequestTimeoutMs = 190_000;

export type IpcChannel = (typeof IPC_CHANNELS)[keyof typeof IPC_CHANNELS];

export type LocalServiceMode = "attached" | "managed";
export type DesktopAuthMode = "backend" | "developmentBypass";

interface DesktopStatusBase {
  serviceMode: LocalServiceMode;
  serviceRunning: boolean | "unknown";
  apiBaseUrl: string;
}

/** Development fixtures are enabled only in the trusted Electron auth-bypass mode. */
export type DesktopStatus =
  | (DesktopStatusBase & { authMode: "backend"; examplesEnabled: false })
  | (DesktopStatusBase & { authMode: "developmentBypass"; examplesEnabled: true });

export type UploadKind = "inspectionReport" | "sitePhotograph";
export type UploadMimeType =
  | "application/pdf"
  | "image/jpeg"
  | "image/png"
  | "text/x-python"
  | "text/csv"
  | "application/json"
  | "text/plain";

/** Metadata for a user-selected input. Its contents have not been inspected or verified. */
export interface SelectedUploadFile {
  /** Opaque Electron-owned handle; never a local filesystem path. */
  uploadToken: string;
  name: string;
  kind: UploadKind;
  mimeType: UploadMimeType;
  sizeBytes: number;
}

export type UploadSelectionErrorCode =
  | "dialogInProgress"
  | "dialogFailed"
  | "invalidRequest"
  | "invalidSelection"
  | "invalidFileType"
  | "fileUnavailable"
  | "fileTooLarge";

export type UploadSelectionResult =
  | { kind: "selected"; file: SelectedUploadFile }
  | { kind: "cancelled" }
  | { kind: "error"; code: UploadSelectionErrorCode; limitBytes?: number };

/** Metadata for a generic chat attachment. It does not identify the source path or expose file contents. */
export interface SelectedChatAttachment {
  /** Opaque Electron-owned handle; never a local filesystem path. */
  uploadToken: string;
  name: string;
  mimeType: UploadMimeType;
  sizeBytes: number;
}

export type ChatAttachmentSelectionErrorCode =
  | "dialogInProgress"
  | "dialogFailed"
  | "invalidFileType"
  | "fileUnavailable"
  | "tooManyFiles"
  | "fileTooLarge"
  | "totalSizeExceeded";

export type ChatAttachmentSelectionResult =
  | { kind: "selected"; files: SelectedChatAttachment[] }
  | { kind: "cancelled" }
  | {
      kind: "error";
      code: ChatAttachmentSelectionErrorCode;
      limitBytes?: number;
      limitCount?: number;
    };

export type LocalServiceRequest =
  | { operation: "health" }
  | { operation: "login"; request: EmployeeLoginRequest }
  | { operation: "restoreSession" }
  | { operation: "logout" }
  | { operation: "chatListSessions" }
  | { operation: "chatCreateSession"; request: ChatSessionCreateRequest }
  | { operation: "chatGetSession"; sessionId: string }
  | { operation: "chatListMessages"; sessionId: string }
  | { operation: "chatAppendMessage"; sessionId: string; request: ChatMessageAppendRequest }
  | { operation: "conversationCreate"; sessionId: string; request: ConversationCreateRequest }
  | { operation: "pdfView"; sessionId: string }
  | { operation: "pdfDelete"; sessionId: string }
  | { operation: "pdfApprove"; sessionId: string; approvalId: string; approve: boolean; argumentsHash: string }
  | { operation: "pdfArtifact"; sessionId: string; artifactId: string; action: "open" | "save" }
  | { operation: "workflowUpload"; sessionId: string; uploadToken: string };

export interface LocalServiceResponse {
  status: number;
  body: string;
}

export interface DesktopBridge {
  subscribeTurn(request: TurnStreamRequest, onUpdate: (event: TurnStreamEvent) => void): () => void;
  getDesktopStatus(): Promise<DesktopStatus>;
  requestLocalService(request: LocalServiceRequest): Promise<LocalServiceResponse>;
  selectUploadFiles(requestedKind: UploadKind): Promise<UploadSelectionResult>;
  selectChatAttachments(workflowType: ChatWorkflowType): Promise<ChatAttachmentSelectionResult>;
  subscribeSessionEvents(
    sessionId: string,
    afterEventId: number,
    onUpdate: (update: SessionEventStreamUpdate) => void,
  ): () => void;
}

export interface EmployeeLoginRequest {
  username: string;
  password: string;
}

export type EmployeeRole = "employee";

export interface EmployeeIdentity {
  employeeId: string;
  username: string;
  displayName: string;
  role: EmployeeRole;
}

/**
 * The local API keeps the authenticated session in its local cookie jar.
 * The renderer does not receive or persist a bearer token.
 */
export interface EmployeeSession {
  sessionId: string;
  user: EmployeeIdentity;
  expiresAt: string;
}

export interface EmployeeLoginResponse {
  session: EmployeeSession;
}

export interface EmployeeSessionRestoreResponse {
  session: EmployeeSession;
}

/** The logout response reports whether FastAPI revoked the authenticated session. */
export interface EmployeeLogoutResponse {
  revoked: boolean;
}

const utcTimestampSchema = z.iso.datetime({ offset: true });

export const modelHealthSchema = z.strictObject({
  capability: z.enum(["text", "vision", "embedding"]),
  status: z.enum(["ready", "missing", "unavailable", "error"]),
  installed: z.boolean(),
  loadable: z.boolean().nullable(),
  selectedModel: z.string().nullable(),
  fallbackReason: z.string().nullable(),
  lastError: z.string().nullable(),
});

export const subsystemReadinessSchema = z.strictObject({
  ready: z.boolean(),
  detail: z.string().max(500).nullable(),
});

export const localDeploymentProofSchema = z.strictObject({
  storageBackend: z.string(),
  persistentStorageLocal: z.boolean(),
  knowledgeStorageLocal: z.boolean(),
  artifactStorageLocal: z.boolean(),
  modelEndpointClassification: z.string(),
  sandboxNetworkPolicy: z.string(),
  sandboxPullPolicy: z.string(),
  pdfConverterMode: z.string(),
  pdfConverterAvailable: z.boolean(),
  dockerAvailable: z.boolean(),
  externalTelemetryConfigured: z.boolean(),
});

/** Canonical camelCase wire contract returned by FastAPI for both 200 and 503 health responses. */
export const healthResponseSchema = z.strictObject({
  status: z.enum(["ready", "degraded"]),
  service: z.literal("workbench-ai"),
  apiVersion: z.literal("v1"),
  localOnly: z.literal(true),
  externalApiCount: z.literal(0),
  ai: z.strictObject({
    runtimeReady: z.boolean(),
    runtimeError: z.string().nullable(),
    models: z.array(modelHealthSchema),
    knowledgeReady: z.boolean(),
    knowledgeError: z.string().nullable(),
  }),
  storage: subsystemReadinessSchema,
  sandbox: subsystemReadinessSchema,
  audit: subsystemReadinessSchema,
  outboundNetworkBlocked: z.boolean(),
  deploymentProof: localDeploymentProofSchema.nullable(),
  checkedAt: utcTimestampSchema,
});

export type ModelHealth = z.infer<typeof modelHealthSchema>;
export type SubsystemReadiness = z.infer<typeof subsystemReadinessSchema>;
export type HealthResponse = z.infer<typeof healthResponseSchema>;

/** Pending FastAPI workflow response contracts. Do not treat fixture data as these results. */
export type WorkflowStageName = "upload" | "extraction" | "retrieval" | "drafting" | "validation";
export type WorkflowStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface WorkflowStage {
  name: WorkflowStageName;
  status: WorkflowStatus;
  label?: string;
}

/** A concise, user-safe workflow event. This intentionally has no reasoning or prompt fields. */
export interface WorkflowActivityEvent {
  eventId: string;
  stage: WorkflowStageName;
  status: WorkflowStatus;
  summary: string;
  occurredAt?: string;
}

export interface WorkflowCitation {
  citationId: string;
  documentTitle: string;
  pageNumber?: number;
  section?: string;
}

export interface WorkflowFinding {
  findingId: string;
  title: string;
  summary: string;
  uncertainty?: string;
  citationIds: readonly string[];
}

export interface WorkflowUploadProgress {
  uploadId: string;
  fileName: string;
  bytesUploaded: number;
  totalBytes: number;
  status: Exclude<WorkflowStatus, "queued">;
}

/** Wire contracts mirroring the local FastAPI chat surface. FastAPI serializes camelCase. */

/** Session kinds; "localConversation" backs plain chat and never runs workflows. */
export const chatWorkflowTypeSchema = z.enum(["inspectionAnalysis", "codeRepair", "localConversation", "pdfDocument"]);

export const chatStageSchema = z.enum([
  "ready",
  "collectingInputs",
  "extracting",
  "retrieving",
  "drafting",
  "validating",
  "planning",
  "awaitingApproval",
  "exporting",
  "sandboxExecuting",
  "repairing",
  "approvalRejected",
  "completed",
  "failed",
]);

export const chatSessionStatusSchema = z.enum(["active", "completed", "failed", "approvalRejected"]);

export const chatMessageRoleSchema = z.enum(["user", "assistant"]);

/** Backend timestamps are timezone-aware UTC ISO 8601. */
export const chatTimestampSchema = utcTimestampSchema;

const uuidSchema = z.uuid();

/** One owned chat thread backed by a local workflow session. */
export const chatSessionSchema = z.strictObject({
  sessionId: uuidSchema,
  ownerUserId: uuidSchema,
  workflowType: chatWorkflowTypeSchema,
  title: z.string().min(1).max(200),
  stage: chatStageSchema,
  status: chatSessionStatusSchema,
  createdAt: chatTimestampSchema,
  updatedAt: chatTimestampSchema,
  /** Null for sessions stored before the renderer idempotency key existed. */
  clientSessionId: uuidSchema.nullable(),
});

/** One persisted chat message without model reasoning fields. */
export const chatMessageSchema = z.strictObject({
  messageId: uuidSchema,
  sessionId: uuidSchema,
  authorUserId: uuidSchema.nullable(),
  role: chatMessageRoleSchema,
  content: z.string().min(1).max(20_000),
  createdAt: chatTimestampSchema,
  /** The renderer idempotency key; null for messages stored before the key existed. */
  clientMessageId: uuidSchema.nullable(),
});

export const chatSessionListResponseSchema = z.strictObject({
  sessions: z.array(chatSessionSchema),
});

export const chatMessageListResponseSchema = z.strictObject({
  messages: z.array(chatMessageSchema),
});

export const chatSessionCreateRequestSchema = z.strictObject({
  workflowType: chatWorkflowTypeSchema,
  title: z
    .string()
    .min(1)
    .max(200)
    .refine((title) => title.trim().length > 0, { message: "title must not be blank" }),
  /** Renderer idempotency key: a retried create returns the stored session. */
  clientSessionId: uuidSchema.optional(),
});

export const chatMessageAppendRequestSchema = z.strictObject({
  content: z
    .string()
    .min(1)
    .max(20_000)
    .refine((content) => content.trim().length > 0, { message: "content must not be blank" }),
  /** Stable per-attempt idempotency key; retries reuse it instead of duplicating. */
  clientMessageId: uuidSchema,
  selectedUploadIds: z.array(uuidSchema).refine((ids) => ids.length === new Set(ids).size, {
    message: "selectedUploadIds must be unique",
  }),
});

export const workflowUploadResponseSchema = z.strictObject({
  uploadId: uuidSchema,
  sessionId: uuidSchema,
  fileName: z.string().min(1).max(255),
  mimeType: z.string().min(1).max(255),
  sizeBytes: z.number().int().positive(),
  sha256: z.string().regex(/^[0-9a-f]{64}$/),
  sourceId: uuidSchema,
  createdAt: chatTimestampSchema,
});

/** One text-only local conversation turn; workflow and retrieval fields are rejected. */
export const conversationMessageMaxLength = 20_000;

export const conversationCreateRequestSchema = z.strictObject({
  message: z
    .string()
    .min(1)
    .max(conversationMessageMaxLength)
    .refine((message) => message.trim().length > 0, { message: "message must not be blank" }),
  /** Stable per-attempt idempotency key so an unconfirmed turn can be reconciled. */
  clientRequestId: uuidSchema.optional(),
});

/** Non-confidential timing and token counts reported with a completed turn. */
export const conversationMetricsSchema = z.strictObject({
  clientElapsedMs: z.number().min(0),
  totalDurationNs: z.number().int().nonnegative().nullable(),
  loadDurationNs: z.number().int().nonnegative().nullable(),
  promptEvalCount: z.number().int().nonnegative().nullable(),
  promptEvalDurationNs: z.number().int().nonnegative().nullable(),
  evalCount: z.number().int().nonnegative().nullable(),
  evalDurationNs: z.number().int().nonnegative().nullable(),
});

/** One persisted conversation turn plus the safe local-model facts to display. */
export const conversationCreateResponseSchema = z.strictObject({
  sessionId: uuidSchema,
  userMessageId: uuidSchema,
  assistantMessageId: uuidSchema,
  assistantText: z.string().min(1).max(20_000),
  /** Bounded model-identifier shape; malformed values fail parsing, not rendering. */
  selectedModel: z
    .string()
    .min(1)
    .max(100)
    .regex(/^[A-Za-z0-9][A-Za-z0-9._:-]*$/),
  usedFallback: z.boolean(),
  fallbackReason: z.string().max(500).nullable(),
  metrics: conversationMetricsSchema.nullable(),
});

export const activityEventTypeSchema = z.enum([
  "session.created",
  "upload.accepted",
  "message.accepted",
  "workflow.stageChanged",
  "workflow.progress",
  "message.completed",
  "approval.required",
  "approval.resolved",
  "artifact.created",
  "sandbox.completed",
  "workflow.failed",
]);

export const sessionActivityEventSchema = z.strictObject({
  eventId: z.number().int().nonnegative(),
  sessionId: uuidSchema,
  workflowRunId: uuidSchema.nullable(),
  eventType: activityEventTypeSchema,
  occurredAt: chatTimestampSchema,
  payload: z.record(z.string(), z.unknown()),
});

export type SessionEventStreamUpdate =
  | { subscriptionId: string; type: "connected" }
  | { subscriptionId: string; type: "event"; event: SessionActivityEvent }
  | { subscriptionId: string; type: "error"; message: string }
  | { subscriptionId: string; type: "closed" };

/** The renderer may build paths only from server-issued session IDs. */
export const chatSessionIdSchema = uuidSchema;

export const chatErrorCodeSchema = z.enum(["session_not_found"]);

export type ChatWorkflowType = z.infer<typeof chatWorkflowTypeSchema>;

export type ChatStage = z.infer<typeof chatStageSchema>;

export type ChatSessionStatus = z.infer<typeof chatSessionStatusSchema>;

export type ChatMessageRole = z.infer<typeof chatMessageRoleSchema>;

export type ChatSession = z.infer<typeof chatSessionSchema>;

export type ChatMessage = z.infer<typeof chatMessageSchema>;

export type ChatSessionListResponse = z.infer<typeof chatSessionListResponseSchema>;

export type ChatMessageListResponse = z.infer<typeof chatMessageListResponseSchema>;

export type ChatSessionCreateRequest = z.infer<typeof chatSessionCreateRequestSchema>;

export type ChatMessageAppendRequest = z.infer<typeof chatMessageAppendRequestSchema>;
export type WorkflowUploadResponse = z.infer<typeof workflowUploadResponseSchema>;
export type ConversationCreateRequest = z.infer<typeof conversationCreateRequestSchema>;
export type ConversationMetrics = z.infer<typeof conversationMetricsSchema>;
export type ConversationCreateResponse = z.infer<typeof conversationCreateResponseSchema>;
export type SessionActivityEvent = z.infer<typeof sessionActivityEventSchema>;
