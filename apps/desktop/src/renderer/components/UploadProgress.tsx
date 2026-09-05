import { X } from "lucide-react";
import type { WorkflowUploadProgress } from "../../shared/contracts";
import { Button } from "./ui/button";

export interface UploadProgressProps {
  upload: WorkflowUploadProgress;
  onCancel?: () => void;
}

function fileSizeLabel(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${Math.round(sizeBytes / 1024)} KB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function UploadProgress({ upload, onCancel }: UploadProgressProps) {
  const totalBytes = Math.max(0, upload.totalBytes);
  const value = totalBytes === 0 ? 0 : Math.min(totalBytes, Math.max(0, upload.bytesUploaded));
  const percent = totalBytes === 0 ? 0 : Math.round((value / totalBytes) * 100);

  return (
    <section aria-label={`Upload ${upload.fileName}`} className="rounded-lg border border-border px-4 py-3" data-slot="upload-progress" data-state={upload.status}>
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-3">
            <p className="truncate text-sm font-medium">{upload.fileName}</p>
            <span className="shrink-0 text-xs text-muted-foreground">{upload.status === "completed" ? "Completed" : `${percent}%`}</span>
          </div>
          <progress aria-label={`${upload.fileName} upload progress`} className="mt-2 h-1.5 w-full accent-foreground" max={totalBytes || 1} value={value}>{percent}%</progress>
          <p className="mt-1 text-xs text-muted-foreground">{fileSizeLabel(value)} of {fileSizeLabel(totalBytes)} · {upload.status}</p>
        </div>
        {onCancel && upload.status === "running" && <Button aria-label={`Cancel upload of ${upload.fileName}`} className="size-8 shrink-0 p-0" onClick={onCancel} size="icon" variant="ghost"><X aria-hidden="true" className="size-4" strokeWidth={1.75} /></Button>}
      </div>
    </section>
  );
}
