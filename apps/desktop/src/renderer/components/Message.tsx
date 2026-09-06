import type { ChatMessage } from "../../shared/contracts";

export interface MessageProps {
  message: ChatMessage;
}

const roleLabels: Record<ChatMessage["role"], string> = {
  user: "Employee",
  assistant: "Assistant",
};

function formatTimestamp(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

/** Plain text only. Model-authored citation-like text is not interpreted or linked here. */
export function Message({ message }: MessageProps) {
  const isEmployee = message.role === "user";

  return (
    <article
      aria-label={`${roleLabels[message.role]} message`}
      className={`max-w-[48rem] ${isEmployee ? "ml-auto" : "mr-auto"}`}
      data-slot="message"
      data-author={message.role}
    >
      <div className={`rounded-lg border px-4 py-3 text-sm leading-6 ${isEmployee ? "border-foreground/15 bg-muted" : "border-border bg-background"}`}>
        <p className="whitespace-pre-wrap break-words">{message.content}</p>
      </div>
      {message.createdAt && (
        <p className={`mt-1 text-xs text-muted-foreground ${isEmployee ? "text-right" : "text-left"}`}>
          <time dateTime={message.createdAt}>{formatTimestamp(message.createdAt)}</time>
        </p>
      )}
    </article>
  );
}
