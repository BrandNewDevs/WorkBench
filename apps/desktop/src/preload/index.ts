import { contextBridge, ipcRenderer } from "electron";
import { turnStreamEventSchema } from "../shared/pdf";
import {
  IPC_CHANNELS,
  type DesktopBridge,
  type ChatAttachmentSelectionResult,
  type ChatWorkflowType,
  type DesktopStatus,
  type LocalServiceRequest,
  type LocalServiceResponse,
  type UploadKind,
  type UploadSelectionResult,
  type SessionEventStreamUpdate,
} from "../shared/contracts";

function invoke<T>(channel: string, ...args: readonly unknown[]): Promise<T> {
  return ipcRenderer.invoke(channel, ...args) as Promise<T>;
}

const bridge: DesktopBridge = {
  subscribeTurn: (request, onUpdate) => {
    const subscriptionId = globalThis.crypto.randomUUID();
    const listener = (_event: Electron.IpcRendererEvent, update: { subscriptionId: string; event: unknown }) => {
      if (update.subscriptionId !== subscriptionId) return;
      const parsed = turnStreamEventSchema.safeParse(update.event);
      if (parsed.success) onUpdate(parsed.data);
    };
    ipcRenderer.on(IPC_CHANNELS.turnEvent, listener);
    ipcRenderer.send(IPC_CHANNELS.startTurn, { subscriptionId, request });
    return () => {
      ipcRenderer.removeListener(IPC_CHANNELS.turnEvent, listener);
      ipcRenderer.send(IPC_CHANNELS.stopSessionEvents, { subscriptionId });
    };
  },
  getDesktopStatus: (): Promise<DesktopStatus> => invoke(IPC_CHANNELS.getDesktopStatus),
  requestLocalService: (request: LocalServiceRequest): Promise<LocalServiceResponse> =>
    invoke(IPC_CHANNELS.requestLocalService, request),
  selectUploadFiles: (requestedKind: UploadKind): Promise<UploadSelectionResult> =>
    invoke(IPC_CHANNELS.selectUploadFiles, requestedKind),
  selectChatAttachments: (workflowType: ChatWorkflowType): Promise<ChatAttachmentSelectionResult> =>
    invoke(IPC_CHANNELS.selectChatAttachments, workflowType),
  subscribeSessionEvents: (
    sessionId: string,
    afterEventId: number,
    onUpdate: (update: SessionEventStreamUpdate) => void,
  ): (() => void) => {
    const subscriptionId = globalThis.crypto.randomUUID();
    const listener = (_event: Electron.IpcRendererEvent, update: SessionEventStreamUpdate) => {
      if (update.subscriptionId === subscriptionId) onUpdate(update);
    };
    ipcRenderer.on(IPC_CHANNELS.sessionEvent, listener);
    ipcRenderer.send(IPC_CHANNELS.startSessionEvents, { subscriptionId, sessionId, afterEventId });
    return () => {
      ipcRenderer.removeListener(IPC_CHANNELS.sessionEvent, listener);
      ipcRenderer.send(IPC_CHANNELS.stopSessionEvents, { subscriptionId });
    };
  },
};

contextBridge.exposeInMainWorld("workbench", bridge);
