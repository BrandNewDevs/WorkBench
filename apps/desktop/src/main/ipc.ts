import { promises as fs } from "node:fs";
import { randomUUID } from "node:crypto";
import { basename, extname } from "node:path";
import { dialog, ipcMain, type IpcMainEvent, type IpcMainInvokeEvent, type OpenDialogReturnValue } from "electron";
import {
  IPC_CHANNELS,
  type ChatAttachmentSelectionResult,
  type ChatWorkflowType,
  type DesktopStatus,
  type LocalServiceRequest,
  type LocalServiceResponse,
  type SelectedChatAttachment,
  type UploadKind,
  type UploadMimeType,
  type UploadSelectionResult,
} from "../shared/contracts";

const MAX_UPLOAD_BYTES = 100 * 1024 * 1024;
const MAX_CHAT_ATTACHMENT_COUNT = 10;
const MAX_CHAT_ATTACHMENT_TOTAL_BYTES = 250 * 1024 * 1024;
let uploadDialogActive = false;
const selectedUploadPaths = new Map<string, string>();

export function resolveSelectedUploadPath(uploadToken: string): string | undefined {
  return selectedUploadPaths.get(uploadToken);
}

function registerSelectedPath(filePath: string): string {
  const uploadToken = randomUUID();
  selectedUploadPaths.set(uploadToken, filePath);
  return uploadToken;
}

interface DesktopIpcDependencies {
  getDesktopStatus: () => DesktopStatus;
  isTrustedSender: (event: IpcMainInvokeEvent | IpcMainEvent) => boolean;
  requestLocalService: (request: LocalServiceRequest) => Promise<LocalServiceResponse>;
  startSessionEvents: (subscriptionId: string, sessionId: string, afterEventId: number, event: IpcMainEvent) => void;
  stopSessionEvents: (subscriptionId: string, event: IpcMainEvent) => void;
}

interface UploadDialogConfig {
  title: string;
  filterName: string;
  extensions: readonly string[];
  mimeTypes: Readonly<Record<string, UploadMimeType>>;
}

const uploadDialogConfigs: Readonly<Record<UploadKind, UploadDialogConfig>> = {
  inspectionReport: {
    title: "Select inspection report",
    filterName: "PDF inspection reports",
    extensions: ["pdf"],
    mimeTypes: { ".pdf": "application/pdf" },
  },
  sitePhotograph: {
    title: "Select site photograph",
    filterName: "Site photographs",
    extensions: ["jpg", "jpeg", "png"],
    mimeTypes: {
      ".jpg": "image/jpeg",
      ".jpeg": "image/jpeg",
      ".png": "image/png",
    },
  },
};

const inspectionAttachmentConfig: UploadDialogConfig = {
  title: "Attach files",
  filterName: "PDF documents and inspection images",
  extensions: ["pdf", "jpg", "jpeg", "png"],
  mimeTypes: {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
  },
};

const codeAttachmentConfig: UploadDialogConfig = {
  title: "Attach source files",
  filterName: "Python, CSV, JSON, and text files",
  extensions: ["py", "csv", "json", "txt"],
  mimeTypes: {
    ".py": "text/x-python",
    ".csv": "text/csv",
    ".json": "application/json",
    ".txt": "text/plain",
  },
};

function isUploadKind(value: unknown): value is UploadKind {
  return value === "inspectionReport" || value === "sitePhotograph";
}

function mimeTypeFor(filePath: string, config: UploadDialogConfig): UploadMimeType | undefined {
  return config.mimeTypes[extname(filePath).toLowerCase()];
}

async function selectUploadFile(requestedKind: unknown): Promise<UploadSelectionResult> {
  if (!isUploadKind(requestedKind)) {
    return { kind: "error", code: "invalidRequest" };
  }
  if (uploadDialogActive) {
    return { kind: "error", code: "dialogInProgress" };
  }

  const config = uploadDialogConfigs[requestedKind];
  uploadDialogActive = true;
  try {
    let result: OpenDialogReturnValue;
    try {
      result = await dialog.showOpenDialog({
        title: config.title,
        properties: ["openFile"],
        filters: [{ name: config.filterName, extensions: [...config.extensions] }],
      });
    } catch {
      return { kind: "error", code: "dialogFailed" };
    }

    if (result.canceled || result.filePaths.length === 0) {
      return { kind: "cancelled" };
    }
    if (result.filePaths.length !== 1) {
      return { kind: "error", code: "invalidSelection" };
    }

    const filePath = result.filePaths[0];
    const mimeType = mimeTypeFor(filePath, config);
    if (!mimeType) {
      return { kind: "error", code: "invalidFileType" };
    }

    let stats: Awaited<ReturnType<typeof fs.stat>>;
    try {
      stats = await fs.stat(filePath);
    } catch {
      return { kind: "error", code: "fileUnavailable" };
    }
    if (!stats.isFile()) {
      return { kind: "error", code: "invalidFileType" };
    }
    if (stats.size > MAX_UPLOAD_BYTES) {
      return { kind: "error", code: "fileTooLarge", limitBytes: MAX_UPLOAD_BYTES };
    }

    return {
      kind: "selected",
      file: { uploadToken: registerSelectedPath(filePath), name: basename(filePath), kind: requestedKind, mimeType, sizeBytes: stats.size },
    };
  } finally {
    uploadDialogActive = false;
  }
}

async function selectChatAttachments(workflowType: ChatWorkflowType): Promise<ChatAttachmentSelectionResult> {
  if (uploadDialogActive) {
    return { kind: "error", code: "dialogInProgress" };
  }

  uploadDialogActive = true;
  try {
    let result: OpenDialogReturnValue;
    try {
      const config = workflowType === "codeRepair" ? codeAttachmentConfig : inspectionAttachmentConfig;
      result = await dialog.showOpenDialog({
        title: config.title,
        properties: ["openFile", "multiSelections"],
        filters: [{ name: config.filterName, extensions: [...config.extensions] }],
      });
    } catch {
      return { kind: "error", code: "dialogFailed" };
    }

    if (result.canceled || result.filePaths.length === 0) {
      return { kind: "cancelled" };
    }
    if (result.filePaths.length > MAX_CHAT_ATTACHMENT_COUNT) {
      return { kind: "error", code: "tooManyFiles", limitCount: MAX_CHAT_ATTACHMENT_COUNT };
    }

    const selectedPaths = new Set<string>();
    const files: SelectedChatAttachment[] = [];
    let totalBytes = 0;
    const config = workflowType === "codeRepair" ? codeAttachmentConfig : inspectionAttachmentConfig;
    for (const filePath of result.filePaths) {
      const mimeType = mimeTypeFor(filePath, config);
      if (!mimeType) {
        return { kind: "error", code: "invalidFileType" };
      }

      let stats: Awaited<ReturnType<typeof fs.stat>>;
      let resolvedPath: string;
      try {
        [stats, resolvedPath] = await Promise.all([fs.stat(filePath), fs.realpath(filePath)]);
      } catch {
        return { kind: "error", code: "fileUnavailable" };
      }
      if (!stats.isFile()) {
        return { kind: "error", code: "invalidFileType" };
      }
      if (stats.size > MAX_UPLOAD_BYTES) {
        return { kind: "error", code: "fileTooLarge", limitBytes: MAX_UPLOAD_BYTES };
      }
      if (selectedPaths.has(resolvedPath)) {
        continue;
      }
      if (totalBytes + stats.size > MAX_CHAT_ATTACHMENT_TOTAL_BYTES) {
        return { kind: "error", code: "totalSizeExceeded", limitBytes: MAX_CHAT_ATTACHMENT_TOTAL_BYTES };
      }

      selectedPaths.add(resolvedPath);
      totalBytes += stats.size;
      files.push({ uploadToken: registerSelectedPath(resolvedPath), name: basename(filePath), mimeType, sizeBytes: stats.size });
    }

    return files.length > 0 ? { kind: "selected", files } : { kind: "cancelled" };
  } finally {
    uploadDialogActive = false;
  }
}

function assertTrustedSender(event: IpcMainInvokeEvent, dependencies: DesktopIpcDependencies): void {
  if (!dependencies.isTrustedSender(event)) {
    throw new Error("This IPC request did not come from the WorkBench window");
  }
}

function assertTrustedEventSender(event: IpcMainEvent, dependencies: DesktopIpcDependencies): void {
  if (!dependencies.isTrustedSender(event)) {
    throw new Error("This IPC request did not come from the WorkBench window");
  }
}

export function registerDesktopIpc(dependencies: DesktopIpcDependencies): void {
  ipcMain.handle(IPC_CHANNELS.getDesktopStatus, (event) => {
    assertTrustedSender(event, dependencies);
    return dependencies.getDesktopStatus();
  });
  ipcMain.handle(IPC_CHANNELS.requestLocalService, (event, request: LocalServiceRequest) => {
    assertTrustedSender(event, dependencies);
    return dependencies.requestLocalService(request);
  });
  ipcMain.handle(IPC_CHANNELS.selectUploadFiles, (event, requestedKind: unknown) => {
    assertTrustedSender(event, dependencies);
    return selectUploadFile(requestedKind);
  });
  ipcMain.handle(IPC_CHANNELS.selectChatAttachments, (event, workflowType: ChatWorkflowType) => {
    assertTrustedSender(event, dependencies);
    if (workflowType !== "inspectionAnalysis" && workflowType !== "codeRepair") {
      return { kind: "error", code: "invalidFileType" } satisfies ChatAttachmentSelectionResult;
    }
    return selectChatAttachments(workflowType);
  });
  ipcMain.on(IPC_CHANNELS.startSessionEvents, (event, request: { subscriptionId?: unknown; sessionId?: unknown; afterEventId?: unknown }) => {
    assertTrustedEventSender(event, dependencies);
    if (
      typeof request?.subscriptionId !== "string" ||
      typeof request.sessionId !== "string" ||
      typeof request.afterEventId !== "number" ||
      !Number.isSafeInteger(request.afterEventId) ||
      request.afterEventId < 0
    ) return;
    dependencies.startSessionEvents(request.subscriptionId, request.sessionId, request.afterEventId, event);
  });
  ipcMain.on(IPC_CHANNELS.stopSessionEvents, (event, request: { subscriptionId?: unknown }) => {
    assertTrustedEventSender(event, dependencies);
    if (typeof request?.subscriptionId !== "string") return;
    dependencies.stopSessionEvents(request.subscriptionId, event);
  });
}
