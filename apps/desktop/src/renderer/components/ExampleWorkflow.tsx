import { useCallback, useState } from "react";
import type { WorkflowCitation, WorkflowFinding, WorkflowStatus } from "../../shared/contracts";
import { ActivityTrace } from "./ActivityTrace";
import { FindingsPanel } from "./FindingsPanel";
import { Message } from "./Message";
import { UploadProgress } from "./UploadProgress";
import { WorkflowNotice } from "./WorkflowNotice";
import { WorkflowStageIndicator } from "./WorkflowStageIndicator";

/** Explicitly separate development fixtures from FastAPI results. */
export interface ExampleFixture<T> {
  source: "exampleFixture";
  data: T;
}

const exampleCitations: ExampleFixture<readonly WorkflowCitation[]> = {
  source: "exampleFixture",
  data: [
    { citationId: "example-sop-1", documentTitle: "Example inspection procedure", pageNumber: 4, section: "Review criteria" },
  ],
};

const exampleFindings: ExampleFixture<readonly WorkflowFinding[]> = {
  source: "exampleFixture",
  data: [
    {
      findingId: "example-review-item",
      title: "Example review item",
      summary: "This example shows how a structured finding is presented. It is not a live inspection result.",
      uncertainty: "The example has no document evidence beyond the fixture source metadata.",
      citationIds: ["example-sop-1"],
    },
  ],
};

export function ExampleWorkflow() {
  const [uploadStatus, setUploadStatus] = useState<WorkflowStatus>("running");
  const [noticeStatus, setNoticeStatus] = useState<Extract<WorkflowStatus, "failed" | "cancelled"> | undefined>("failed");

  const retryExample = useCallback(() => {
    setNoticeStatus(undefined);
    setUploadStatus("queued");
  }, []);
  const cancelExampleUpload = useCallback(() => {
    setUploadStatus("cancelled");
    setNoticeStatus("cancelled");
  }, []);

  return (
    <section aria-labelledby="example-workflow-heading" className="mx-auto w-full max-w-3xl py-8" data-slot="example-workflow">
      <header className="border-b border-border pb-5">
        <p className="text-xs font-medium uppercase tracking-[0.08em] text-muted-foreground">Example</p>
        <h1 id="example-workflow-heading" className="mt-1 text-lg font-medium tracking-tight">Workflow component preview</h1>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">Fixture data only. It does not upload files, contact FastAPI, or represent a live workflow.</p>
      </header>

      <div className="mt-6 space-y-6">
        <div className="space-y-3" aria-label="Example messages">
          <Message message={{ messageId: "example-employee", author: "employee", text: "Please review the uploaded inspection report.", createdAt: "2026-02-23T09:15:00Z" }} />
          <Message message={{ messageId: "example-assistant", author: "assistant", text: "Example response. Structured findings and source metadata appear below when supplied by the application.", status: "completed" }} />
        </div>

        <WorkflowStageIndicator stages={[
          { name: "upload", status: uploadStatus },
          { name: "extraction", status: "queued" },
          { name: "retrieval", status: "queued" },
          { name: "drafting", status: "queued" },
          { name: "validation", status: "queued" },
        ]} />

        <UploadProgress
          onCancel={uploadStatus === "running" ? cancelExampleUpload : undefined}
          upload={{ uploadId: "example-upload", fileName: "example-inspection-report.pdf", bytesUploaded: uploadStatus === "running" ? 734_003 : 0, totalBytes: 1_048_576, status: uploadStatus === "queued" ? "running" : uploadStatus === "completed" ? "completed" : uploadStatus === "failed" ? "failed" : uploadStatus === "cancelled" ? "cancelled" : "running" }}
        />

        {noticeStatus && <WorkflowNotice message={noticeStatus === "failed" ? "Example failure: the local workflow did not return a result." : "Example upload cancellation requested. No file was uploaded."} onRetry={noticeStatus === "failed" ? retryExample : undefined} status={noticeStatus} />}

        <ActivityTrace events={[
          { eventId: "example-upload", stage: "upload", status: uploadStatus, summary: "Example upload status", occurredAt: "2026-02-23T09:15:03Z" },
          { eventId: "example-extract", stage: "extraction", status: "queued", summary: "Example extraction is queued" },
        ]} />

        <FindingsPanel citations={exampleCitations.data} findings={exampleFindings.data} />
      </div>
    </section>
  );
}
