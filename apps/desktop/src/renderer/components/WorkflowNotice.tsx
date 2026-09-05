import { AlertCircle, RotateCcw, X } from "lucide-react";
import type { WorkflowStatus } from "../../shared/contracts";
import { Button } from "./ui/button";

export interface WorkflowNoticeProps {
  status: Extract<WorkflowStatus, "failed" | "cancelled">;
  message: string;
  onCancel?: () => void;
  onRetry?: () => void;
  title?: string;
}

export function WorkflowNotice({ status, message, onCancel, onRetry, title }: WorkflowNoticeProps) {
  const resolvedTitle = title ?? (status === "failed" ? "Workflow failed" : "Workflow cancelled");

  return (
    <section aria-live="polite" className="rounded-lg border border-foreground/20 bg-muted/50 px-4 py-3" data-slot="workflow-notice" data-state={status} role="status">
      <div className="flex gap-3">
        <AlertCircle aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted-foreground" strokeWidth={1.75} />
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-medium">{resolvedTitle}</h2>
          <p className="mt-1 text-sm leading-5 text-muted-foreground">{message}</p>
          {(onRetry || onCancel) && (
            <div className="mt-3 flex flex-wrap gap-2">
              {onRetry && <Button onClick={onRetry} size="sm" variant="outline"><RotateCcw aria-hidden="true" className="size-3.5" strokeWidth={1.75} />Retry</Button>}
              {onCancel && <Button onClick={onCancel} size="sm" variant="ghost"><X aria-hidden="true" className="size-3.5" strokeWidth={1.75} />Cancel</Button>}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
