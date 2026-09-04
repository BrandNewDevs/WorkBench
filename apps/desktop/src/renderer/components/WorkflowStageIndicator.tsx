import { Check, Circle, LoaderCircle, OctagonX } from "lucide-react";
import type { WorkflowStage, WorkflowStatus } from "../../shared/contracts";

export interface WorkflowStageIndicatorProps {
  stages: readonly WorkflowStage[];
}

const stageLabels: Record<WorkflowStage["name"], string> = {
  upload: "Upload",
  extraction: "Extraction",
  retrieval: "Retrieval",
  drafting: "Drafting",
  validation: "Validation",
};

function StageIcon({ status }: { status: WorkflowStatus }) {
  if (status === "completed") return <Check aria-hidden="true" className="size-3.5" strokeWidth={2} />;
  if (status === "failed" || status === "cancelled") return <OctagonX aria-hidden="true" className="size-3.5" strokeWidth={1.75} />;
  if (status === "running") return <LoaderCircle aria-hidden="true" className="size-3.5 animate-spin" strokeWidth={1.75} />;
  return <Circle aria-hidden="true" className="size-3.5" strokeWidth={1.75} />;
}

export function WorkflowStageIndicator({ stages }: WorkflowStageIndicatorProps) {
  return (
    <ol aria-label="Workflow stages" className="flex flex-wrap gap-x-4 gap-y-2" data-slot="workflow-stage-indicator">
      {stages.map((stage) => (
        <li className="inline-flex items-center gap-1.5 text-xs text-muted-foreground" data-state={stage.status} key={stage.name}>
          <span className="inline-flex size-4 items-center justify-center" aria-label={`${stage.label ?? stageLabels[stage.name]}: ${stage.status}`} role="img">
            <StageIcon status={stage.status} />
          </span>
          <span>{stage.label ?? stageLabels[stage.name]}</span>
        </li>
      ))}
    </ol>
  );
}
