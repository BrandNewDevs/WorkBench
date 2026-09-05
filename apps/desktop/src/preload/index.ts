import { contextBridge, ipcRenderer } from "electron";
import {
  IPC_CHANNELS,
  type DesktopBridge,
  type ChatAttachmentSelectionResult,
  type DesktopStatus,
  type LocalServiceRequest,
  type LocalServiceResponse,
  type UploadKind,
  type UploadSelectionResult,
} from "../shared/contracts";

function invoke<T>(channel: string, ...args: readonly unknown[]): Promise<T> {
  return ipcRenderer.invoke(channel, ...args) as Promise<T>;
}

const bridge: DesktopBridge = {
  getDesktopStatus: (): Promise<DesktopStatus> => invoke(IPC_CHANNELS.getDesktopStatus),
  requestLocalService: (request: LocalServiceRequest): Promise<LocalServiceResponse> =>
    invoke(IPC_CHANNELS.requestLocalService, request),
  selectUploadFiles: (requestedKind: UploadKind): Promise<UploadSelectionResult> =>
    invoke(IPC_CHANNELS.selectUploadFiles, requestedKind),
  selectChatAttachments: (): Promise<ChatAttachmentSelectionResult> =>
    invoke(IPC_CHANNELS.selectChatAttachments),
};

contextBridge.exposeInMainWorld("workbench", bridge);
