import { promises as fs } from "node:fs";
import { basename, extname } from "node:path";
import { dialog, ipcMain, type IpcMainInvokeEvent, type OpenDialogReturnValue } from "electron";
import {
  IPC_CHANNELS,
  type ChatAttachmentSelectionResult,
  type DesktopStatus,
  type SelectedChatAttachment,
  type UploadKind,
  type UploadMimeType,
  type UploadSelectionResult,
} from "../shared/contracts";

const MAX_UPLOAD_BYTES = 100 * 1024 * 1024;
const MAX_CHAT_ATTACHMENT_COUNT = 10;
const MAX_CHAT_ATTACHMENT_TOTAL_BYTES = 250 * 1024 * 1024;
let uploadDialogActive = false;

interface DesktopIpcDependencies {
  getDesktopStatus: () => DesktopStatus;
  isTrustedSender: (event: IpcMainInvokeEvent) => boolean;
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
    extensions: ["jpg", "jpeg", "png", "webp"],
    mimeTypes: {
      ".jpg": "image/jpeg",
      ".jpeg": "image/jpeg",
      ".png": "image/png",
      ".webp": "image/webp",
    },
  },
};

const chatAttachmentConfig: UploadDialogConfig = {
  title: "Attach files",
  filterName: "PDF documents and inspection images",
  extensions: ["pdf", "jpg", "jpeg", "png", "webp"],
  mimeTypes: {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
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
      file: { name: basename(filePath), kind: requestedKind, mimeType, sizeBytes: stats.size },
    };
  } finally {
    uploadDialogActive = false;
  }
}

async function selectChatAttachments(): Promise<ChatAttachmentSelectionResult> {
  if (uploadDialogActive) {
    return { kind: "error", code: "dialogInProgress" };
  }

  uploadDialogActive = true;
  try {
    let result: OpenDialogReturnValue;
    try {
      result = await dialog.showOpenDialog({
        title: chatAttachmentConfig.title,
        properties: ["openFile", "multiSelections"],
        filters: [{ name: chatAttachmentConfig.filterName, extensions: [...chatAttachmentConfig.extensions] }],
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
    for (const filePath of result.filePaths) {
      const mimeType = mimeTypeFor(filePath, chatAttachmentConfig);
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
      files.push({ name: basename(filePath), mimeType, sizeBytes: stats.size });
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

export function registerDesktopIpc(dependencies: DesktopIpcDependencies): void {
  ipcMain.handle(IPC_CHANNELS.getDesktopStatus, (event) => {
    assertTrustedSender(event, dependencies);
    return dependencies.getDesktopStatus();
  });
  ipcMain.handle(IPC_CHANNELS.selectUploadFiles, (event, requestedKind: unknown) => {
    assertTrustedSender(event, dependencies);
    return selectUploadFile(requestedKind);
  });
  ipcMain.handle(IPC_CHANNELS.selectChatAttachments, (event) => {
    assertTrustedSender(event, dependencies);
    return selectChatAttachments();
  });
}
