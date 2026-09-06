import { Check, Circle, LoaderCircle, OctagonX } from "lucide-react";
import type { ChatSessionStatus, ChatStage, ChatWorkflowType } from "../../shared/contracts";
import { chatSessionStatusLabel, chatStageLabels, chatStageSteps, type ChatStageStep } from "../lib/chatThreads";

export interface SessionStageStripProps {
  stage: ChatStage;
  status: ChatSessionStatus;
  workflowType: ChatWorkflowType;
}

function StepIcon({ state }: { state: ChatStageStep["state"] }) {
  if (state === "done") return <Check aria-hidden="true" className="size-3.5" strokeWidth={2} />;
  if (state === "failed") return <OctagonX aria-hidden="true" className="size-3.5" strokeWidth={1.75} />;
  if (state === "active") return <LoaderCircle aria-hidden="true" className="size-3.5 animate-spin" strokeWidth={1.75} />;
  return <Circle aria-hidden="true" className="size-3.5" strokeWidth={1.75} />;
}

function stepClassName(state: ChatStageStep["state"]): string {
  if (state === "failed") return "text-destructive";
  if (state === "active") return "text-foreground";
  return "text-muted-foreground";
}

/** Live, backend-reported workflow progress. The renderer never invents a stage. */
export function SessionStageStrip({ stage, status, workflowType }: SessionStageStripProps) {
  const steps = chatStageSteps(workflowType, stage, status);
  const terminalStatus = status === "active" ? undefined : chatSessionStatusLabel[status];

  return (
    <div aria-label="Workflow status" className="flex flex-wrap items-center gap-x-4 gap-y-2" data-slot="session-stage-strip">
      {steps.map((step) => (
        <span className={`inline-flex items-center gap-1.5 text-xs ${stepClassName(step.state)}`} data-state={step.state} key={step.stage}>
          <span aria-label={`${chatStageLabels[step.stage]}: ${step.state}`} className="inline-flex size-4 items-center justify-center" role="img">
            <StepIcon state={step.state} />
          </span>
          <span>{chatStageLabels[step.stage]}</span>
        </span>
      ))}
      {terminalStatus && (
        <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${status === "failed" || status === "approvalRejected" ? "border-destructive/30 text-destructive" : "border-border text-muted-foreground"}`}>
          {terminalStatus}
        </span>
      )}
    </div>
  );
}
