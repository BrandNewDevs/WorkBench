import { lazy, Suspense, useCallback, useState } from "react";
import { FileText, Image, Paperclip, Send, X } from "lucide-react";
import type {
  ChatAttachmentSelectionResult,
  SelectedChatAttachment,
  SelectedUploadFile,
  UploadKind,
  UploadSelectionResult,
} from "../../shared/contracts";
import type { ChatThread, ChatThreadId } from "../hooks/useChatThreads";
import { Button } from "./ui/button";
import { Label } from "./ui/label";
import { Textarea } from "./ui/textarea";
/** Keep fixture construction out of normal authenticated workspaces. */
const ExampleWorkflow = lazy(async () => {
  const module = await import("./ExampleWorkflow");
  return { default: module.ExampleWorkflow };
});

type ChatPageProps = {
  examplesEnabled: boolean;
  onAttachmentsChange: (threadId: ChatThreadId, attachments: readonly SelectedChatAttachment[]) => void;
  onDraftChange: (threadId: ChatThreadId, draft: string) => void;
  onInspectionFilesChange: (threadId: ChatThreadId, kind: UploadKind, file?: SelectedUploadFile) => void;
  thread: ChatThread;
};

type SelectedFile =
  | { source: "inspectionStarter"; file: SelectedUploadFile }
  | { source: "chatAttachment"; file: SelectedChatAttachment };

type FileSelectionProps = {
  selection: SelectedFile;
  disabled: boolean;
  onRemove: () => void;
  onReplace?: (kind: UploadKind) => void;
};

function fileKindLabel(kind: UploadKind): string {
  return kind === "inspectionReport" ? "Inspection report" : "Site photograph";
}

function fileSizeLabel(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${Math.round(sizeBytes / 1024)} KB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

function selectionErrorMessage(result: Extract<UploadSelectionResult, { kind: "error" }>): string {
  switch (result.code) {
    case "dialogInProgress":
      return "A file selection dialog is already open.";
    case "dialogFailed":
      return "The file selection dialog could not be opened. Try again.";
    case "invalidRequest":
      return "That file selection request is not available.";
    case "invalidSelection":
      return "Choose exactly one file.";
    case "invalidFileType":
      return "Choose a file that matches the requested input type.";
    case "fileUnavailable":
      return "The selected file is no longer available.";
    case "fileTooLarge":
      return `Choose a file no larger than ${fileSizeLabel(result.limitBytes ?? 0)}.`;
  }
}

function attachmentSelectionErrorMessage(result: Extract<ChatAttachmentSelectionResult, { kind: "error" }>): string {
  switch (result.code) {
    case "dialogInProgress":
      return "A file selection dialog is already open.";
    case "dialogFailed":
      return "The file selection dialog could not be opened. Try again.";
    case "invalidFileType":
      return "Attachments must be PDF documents or JPG, PNG, or WebP images.";
    case "fileUnavailable":
      return "One or more selected files are no longer available.";
    case "tooManyFiles":
      return `Choose no more than ${result.limitCount ?? 0} files at once.`;
    case "fileTooLarge":
      return `Each file must be no larger than ${fileSizeLabel(result.limitBytes ?? 0)}.`;
    case "totalSizeExceeded":
      return `The selected files must total no more than ${fileSizeLabel(result.limitBytes ?? 0)}.`;
  }
}

function attachmentKey(file: SelectedChatAttachment): string {
  return `${file.name.toLowerCase()}\u0000${file.mimeType}\u0000${file.sizeBytes}`;
}

type ChatComposerProps = {
  draft: string;
  isSelecting: boolean;
  onDraftChange: (draft: string) => void;
  onSelectAttachments: () => void;
};

function ChatComposer({ draft, isSelecting, onDraftChange, onSelectAttachments }: ChatComposerProps) {
  return (
    <div className="relative overflow-hidden rounded-lg border border-border bg-background shadow-sm">
      <Label className="sr-only" htmlFor="chat-draft">Message draft</Label>
      <Textarea
        className="field-sizing-content min-h-16 max-h-48 resize-none overflow-y-auto rounded-none border-0 bg-transparent px-3 py-2.5 pb-12 pr-24 shadow-none focus-visible:border-transparent focus-visible:ring-0"
        id="chat-draft"
        onChange={(event) => onDraftChange(event.target.value)}
        placeholder="Message WorkBench"
        value={draft}
      />
      <div className="absolute bottom-1.5 right-2 flex items-center gap-1">
        <Button
          aria-label="Attach files"
          disabled={isSelecting}
          onClick={onSelectAttachments}
          size="icon"
          type="button"
          variant="ghost"
        >
          <Paperclip aria-hidden="true" className="size-4" strokeWidth={1.75} />
        </Button>
        <Button aria-label="Sending is unavailable in this preview" disabled size="icon" type="button" variant="ghost">
          <Send aria-hidden="true" className="size-4" strokeWidth={1.75} />
        </Button>
      </div>
    </div>
  );
}

function SelectedFileRow({ selection, disabled, onRemove, onReplace }: FileSelectionProps) {
  const { file } = selection;
  const starterFile = selection.source === "inspectionStarter" ? selection.file : undefined;
  const Icon = starterFile?.kind === "sitePhotograph" ? Image : FileText;
  const label = starterFile ? fileKindLabel(starterFile.kind) : "Attachment";

  return (
    <li className="flex min-w-0 items-center gap-3 px-4 py-3.5">
      <Icon aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" strokeWidth={1.75} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-foreground">{file.name}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">{label} · {fileSizeLabel(file.sizeBytes)}</p>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {starterFile && onReplace && (
          <Button className="h-8 px-2.5 text-xs" disabled={disabled} onClick={() => onReplace(starterFile.kind)} type="button" variant="outline">
            Replace
          </Button>
        )}
        <Button
          aria-label={`Remove ${file.name}`}
          className="size-8 p-0"
          disabled={disabled}
          onClick={onRemove}
          type="button"
          variant="ghost"
        >
          <X aria-hidden="true" className="size-4" strokeWidth={1.75} />
        </Button>
      </div>
    </li>
  );
}

export function ChatPage({ examplesEnabled, onAttachmentsChange, onDraftChange, onInspectionFilesChange, thread }: ChatPageProps) {
  const [selectionMessage, setSelectionMessage] = useState<string | undefined>();
  const [selecting, setSelecting] = useState(false);
  const threadId = thread.id;
  const { attachments, draft, inspectionFiles } = thread;
  const selectedFiles: SelectedFile[] = [
    ...Object.values(inspectionFiles).filter((file): file is SelectedUploadFile => file !== undefined).map((file) => ({ source: "inspectionStarter" as const, file })),
    ...attachments.map((file) => ({ source: "chatAttachment" as const, file })),
  ];

  const selectInspectionFile = useCallback(async (requestedKind: UploadKind) => {
    if (selecting) return;

    setSelectionMessage(undefined);
    setSelecting(true);
    try {
      const result = await window.workbench.selectUploadFiles(requestedKind);
      if (result.kind === "selected") {
        onInspectionFilesChange(threadId, requestedKind, result.file);
      } else if (result.kind === "error") {
        setSelectionMessage(selectionErrorMessage(result));
      }
    } catch {
      setSelectionMessage("The file selection dialog could not be opened. Try again.");
    } finally {
      setSelecting(false);
    }
  }, [onInspectionFilesChange, selecting, threadId]);

  const selectChatAttachments = useCallback(async () => {
    if (selecting) return;

    setSelectionMessage(undefined);
    setSelecting(true);
    try {
      const result = await window.workbench.selectChatAttachments();
      if (result.kind === "selected") {
        const existingKeys = new Set(attachments.map(attachmentKey));
        const newAttachments = result.files.filter((file) => {
          const key = attachmentKey(file);
          if (existingKeys.has(key)) return false;
          existingKeys.add(key);
          return true;
        });
        if (newAttachments.length > 0) {
          onAttachmentsChange(threadId, [...attachments, ...newAttachments]);
        }
      } else if (result.kind === "error") {
        setSelectionMessage(attachmentSelectionErrorMessage(result));
      }
    } catch {
      setSelectionMessage("The file selection dialog could not be opened. Try again.");
    } finally {
      setSelecting(false);
    }
  }, [attachments, onAttachmentsChange, selecting, threadId]);

  const removeInspectionFile = useCallback((kind: UploadKind) => {
    onInspectionFilesChange(threadId, kind);
  }, [onInspectionFilesChange, threadId]);

  const removeAttachment = useCallback((file: SelectedChatAttachment) => {
    const key = attachmentKey(file);
    onAttachmentsChange(threadId, attachments.filter((attachment) => attachmentKey(attachment) !== key));
  }, [attachments, onAttachmentsChange, threadId]);

  const hasFiles = selectedFiles.length > 0;

  const isExampleThread = examplesEnabled && thread.source === "example";

  return (
    <section className="grid min-h-0 flex-1 grid-rows-[minmax(0,1fr)_auto] px-8 pb-16 pt-10">
      <div className="min-h-0 overflow-y-auto">
        {isExampleThread ? (
          <section aria-label="Example workflow">
            <Suspense fallback={<p className="mx-auto w-full max-w-3xl py-8 text-sm text-muted-foreground">Loading example...</p>}>
              <ExampleWorkflow />
            </Suspense>
          </section>
        ) : (
          <section aria-labelledby="chat-heading" className="flex min-h-full items-center justify-center pb-4">
            <div className={`w-full ${hasFiles ? "max-w-xl" : "max-w-md text-center"}`}>
              <h1 id="chat-heading" className="text-lg font-medium tracking-tight text-foreground">
                {hasFiles ? "Selected files" : "Start an inspection review"}
              </h1>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                {hasFiles
                  ? "These are user-selected inputs for a future upload step. They stay in this open client session, and their contents have not been analyzed or verified."
                  : "Select an inspection report and site photograph, or attach supporting files. This preview records file metadata only and stays in this open client session."}
              </p>
              {hasFiles && (
                <ul aria-label="Selected files" className="mt-5 divide-y divide-border overflow-hidden rounded-lg border border-border bg-muted/30">
                  {selectedFiles.map((selection) => {
                    const key = selection.source === "inspectionStarter" ? selection.file.kind : attachmentKey(selection.file);
                    return (
                      <SelectedFileRow
                        disabled={selecting}
                        key={`${selection.source}:${key}`}
                        onRemove={() => selection.source === "inspectionStarter" ? removeInspectionFile(selection.file.kind) : removeAttachment(selection.file)}
                        onReplace={selection.source === "inspectionStarter" ? (kind) => void selectInspectionFile(kind) : undefined}
                        selection={selection}
                      />
                    );
                  })}
                </ul>
              )}
              <div className={`flex flex-wrap gap-3 ${hasFiles ? "mt-4" : "mt-6 justify-center"}`}>
                {!inspectionFiles.inspectionReport && <Button disabled={selecting} onClick={() => void selectInspectionFile("inspectionReport")} type="button">Select inspection report</Button>}
                {!inspectionFiles.sitePhotograph && <Button disabled={selecting} onClick={() => void selectInspectionFile("sitePhotograph")} type="button" variant={hasFiles ? "outline" : "default"}>Select site photograph</Button>}
              </div>
              {selectionMessage && <p aria-live="polite" className="mt-4 text-sm text-muted-foreground" role="status">{selectionMessage}</p>}
            </div>
          </section>
        )}
      </div>
      <div className="mx-auto w-full max-w-2xl pt-4">
        <ChatComposer
          draft={draft}
          isSelecting={selecting}
          onDraftChange={(nextDraft) => onDraftChange(threadId, nextDraft)}
          onSelectAttachments={() => void selectChatAttachments()}
        />
      </div>
    </section>
  );
}
