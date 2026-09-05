export const IPC_CHANNELS = {
  getDesktopStatus: "desktop:get-status",
  selectUploadFiles: "desktop:select-upload-files",
  selectChatAttachments: "desktop:select-chat-attachments",
  requestLocalService: "desktop:request-local-service",
} as const;

/** The only FastAPI origin the desktop client may contact. */
export const LOCAL_API_ORIGIN = "http://127.0.0.1:8000";

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
export type UploadMimeType = "application/pdf" | "image/jpeg" | "image/png" | "image/webp";

/** Metadata for a user-selected input. Its contents have not been inspected or verified. */
export interface SelectedUploadFile {
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
  | { operation: "chatAppendMessage"; sessionId: string; request: ChatMessageAppendRequest };

export interface LocalServiceResponse {
  status: number;
  body: string;
}

export interface DesktopBridge {
  getDesktopStatus(): Promise<DesktopStatus>;
  requestLocalService(request: LocalServiceRequest): Promise<LocalServiceResponse>;
  selectUploadFiles(requestedKind: UploadKind): Promise<UploadSelectionResult>;
  selectChatAttachments(): Promise<ChatAttachmentSelectionResult>;
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

export type HealthStatus = "healthy" | "degraded";
export type OutboundStatus = "blocked" | "clear" | "unknown";

export interface HealthResponse {
  status: HealthStatus;
  service: "fastapi";
  localInference: boolean;
  currentModel: string | null;
  externalApis: number;
  outboundStatus: OutboundStatus;
  checkedAt: string;
}

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

export interface WorkflowMessage {
  messageId: string;
  author: "employee" | "assistant";
  text: string;
  createdAt?: string;
  status?: WorkflowStatus;
}

export interface WorkflowUploadProgress {
  uploadId: string;
  fileName: string;
  bytesUploaded: number;
  totalBytes: number;
  status: Exclude<WorkflowStatus, "queued">;
}

/** Wire contracts mirroring the local FastAPI chat surface. FastAPI serializes camelCase. */

export type ChatWorkflowType = "inspectionAnalysis" | "codeRepair";

export type ChatStage =
  | "collectingInputs"
  | "extracting"
  | "retrieving"
  | "drafting"
  | "validating"
  | "planning"
  | "awaitingApproval"
  | "exporting"
  | "sandboxExecuting"
  | "repairing"
  | "approvalRejected"
  | "completed"
  | "failed";

export type ChatSessionStatus = "active" | "completed" | "failed" | "approvalRejected";

export type ChatMessageRole = "user" | "assistant";

/** One owned chat thread backed by a local workflow session. */
export interface ChatSession {
  sessionId: string;
  ownerUserId: string;
  workflowType: ChatWorkflowType;
  title: string;
  stage: ChatStage;
  status: ChatSessionStatus;
  createdAt: string;
  updatedAt: string;
}

/** One persisted chat message without model reasoning fields. */
export interface ChatMessage {
  messageId: string;
  sessionId: string;
  authorUserId: string | null;
  role: ChatMessageRole;
  content: string;
  createdAt: string;
}

export interface ChatSessionListResponse {
  sessions: ChatSession[];
}

export interface ChatMessageListResponse {
  messages: ChatMessage[];
}

export interface ChatSessionCreateRequest {
  workflowType: ChatWorkflowType;
  title: string;
}

export interface ChatMessageAppendRequest {
  content: string;
}
