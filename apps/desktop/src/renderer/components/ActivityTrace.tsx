import { Check, Circle, LoaderCircle, OctagonX } from "lucide-react";
import type { WorkflowActivityEvent, WorkflowStatus } from "../../shared/contracts";

export interface ActivityTraceProps {
  events: readonly WorkflowActivityEvent[];
  heading?: string;
}

function StatusIcon({ status }: { status: WorkflowStatus }) {
  if (status === "completed") return <Check aria-hidden="true" className="size-3.5" strokeWidth={2} />;
  if (status === "failed" || status === "cancelled") return <OctagonX aria-hidden="true" className="size-3.5" strokeWidth={1.75} />;
  if (status === "running") return <LoaderCircle aria-hidden="true" className="size-3.5 animate-spin" strokeWidth={1.75} />;
  return <Circle aria-hidden="true" className="size-3.5" strokeWidth={1.75} />;
}

function labelForStatus(status: WorkflowStatus): string {
  return status.slice(0, 1).toUpperCase() + status.slice(1);
}

export function ActivityTrace({ events, heading = "Activity" }: ActivityTraceProps) {
  return (
    <section aria-labelledby="activity-trace-heading" data-slot="activity-trace">
      <h2 id="activity-trace-heading" className="text-sm font-medium">{heading}</h2>
      {events.length === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground">No workflow activity has been reported.</p>
      ) : (
        <ol className="mt-3 divide-y divide-border rounded-lg border border-border">
          {events.map((event) => (
            <li className="flex gap-3 px-3 py-3" data-state={event.status} key={event.eventId}>
              <span className="mt-0.5 inline-flex size-4 shrink-0 items-center justify-center text-muted-foreground">
                <StatusIcon status={event.status} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-sm leading-5 text-foreground">{event.summary}</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  <span className="capitalize">{event.stage}</span> · {labelForStatus(event.status)}
                  {event.occurredAt && <> · <time dateTime={event.occurredAt}>{event.occurredAt}</time></>}
                </p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
