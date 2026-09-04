import type { WorkflowMessage, WorkflowStatus } from "../../shared/contracts";

export interface MessageProps {
  message: WorkflowMessage;
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function statusLabel(status: WorkflowStatus): string {
  return status === "cancelled" ? "Cancelled" : `${status.slice(0, 1).toUpperCase()}${status.slice(1)}`;
}

/** Plain text only. Model-authored citation-like text is not interpreted or linked here. */
export function Message({ message }: MessageProps) {
  const isEmployee = message.author === "employee";

  return (
    <article
      aria-label={`${isEmployee ? "Employee" : "Assistant"} message`}
      className={`max-w-[48rem] ${isEmployee ? "ml-auto" : "mr-auto"}`}
      data-slot="message"
      data-author={message.author}
      data-state={message.status}
    >
      <div className={`rounded-lg border px-4 py-3 text-sm leading-6 ${isEmployee ? "border-foreground/15 bg-muted" : "border-border bg-background"}`}>
        <p className="whitespace-pre-wrap break-words">{message.text}</p>
      </div>
      {(message.createdAt || message.status) && (
        <p className={`mt-1 flex gap-2 text-xs text-muted-foreground ${isEmployee ? "justify-end" : "justify-start"}`}>
          {message.createdAt && <time dateTime={message.createdAt}>{formatTimestamp(message.createdAt)}</time>}
          {message.status && <span>{statusLabel(message.status)}</span>}
        </p>
      )}
    </article>
  );
}
