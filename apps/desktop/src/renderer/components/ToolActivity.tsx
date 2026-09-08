import { Check, LoaderCircle, Wrench } from "lucide-react";
import type { SessionActivityEvent } from "../../shared/contracts";

const labels: Record<SessionActivityEvent["eventType"], string> = {
  "session.created": "Preparing local workspace",
  "upload.accepted": "Reading approved file",
  "message.accepted": "Planning local work",
  "workflow.stageChanged": "Advancing workflow",
  "workflow.progress": "Running local tool",
  "message.completed": "Preparing response",
  "approval.required": "Waiting for approval",
  "approval.resolved": "Approval resolved",
  "artifact.created": "Creating local artifact",
  "sandbox.completed": "Running sandbox",
  "workflow.failed": "Local workflow failed",
};

/** A compact, content-free trace of deterministic local tool activity. */
export function ToolActivity({ events }: { events: readonly SessionActivityEvent[] }) {
  if (events.length === 0) return null;
  const visible = events.slice(-6);
  return (
    <ol aria-label="Local tool activity" className="mb-3 space-y-1.5">
      {visible.map((event) => {
        const complete = ["message.completed", "artifact.created", "sandbox.completed"].includes(event.eventType);
        const failed = event.eventType === "workflow.failed";
        return (
          <li className={`flex items-center gap-2 text-xs ${failed ? "text-destructive" : "text-muted-foreground"}`} key={event.eventId}>
            {complete ? <Check aria-hidden="true" className="size-3.5" /> : event.eventType === "workflow.progress" ? <LoaderCircle aria-hidden="true" className="size-3.5 animate-spin" /> : <Wrench aria-hidden="true" className="size-3.5" />}
            <span>{labels[event.eventType]}</span>
          </li>
        );
      })}
    </ol>
  );
}
