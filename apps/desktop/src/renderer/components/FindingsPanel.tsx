import { BookOpen } from "lucide-react";
import type { WorkflowCitation, WorkflowFinding } from "../../shared/contracts";

export interface FindingsPanelProps {
  citations: readonly WorkflowCitation[];
  findings: readonly WorkflowFinding[];
  heading?: string;
}

function citationLocation(citation: WorkflowCitation): string | undefined {
  const parts = [citation.pageNumber === undefined ? undefined : `Page ${citation.pageNumber}`, citation.section];
  const location = parts.filter((part): part is string => part !== undefined);
  return location.length > 0 ? location.join(" · ") : undefined;
}

export function FindingsPanel({ citations, findings, heading = "Findings" }: FindingsPanelProps) {
  const citationsById = new Map(citations.map((citation) => [citation.citationId, citation]));

  return (
    <section aria-labelledby="findings-heading" data-slot="findings-panel">
      <h2 id="findings-heading" className="text-sm font-medium">{heading}</h2>
      {findings.length === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground">No structured findings are available.</p>
      ) : (
        <ol className="mt-3 space-y-3">
          {findings.map((finding) => {
            const findingCitations = finding.citationIds.flatMap((citationId) => {
              const citation = citationsById.get(citationId);
              return citation ? [citation] : [];
            });
            return (
              <li className="rounded-lg border border-border px-4 py-3" data-slot="finding" key={finding.findingId}>
                <h3 className="text-sm font-medium">{finding.title}</h3>
                <p className="mt-1 text-sm leading-6 text-muted-foreground">{finding.summary}</p>
                {finding.uncertainty && <p className="mt-3 border-l-2 border-border pl-3 text-xs leading-5 text-muted-foreground">Uncertainty: {finding.uncertainty}</p>}
                {findingCitations.length > 0 && (
                  <ul aria-label={`Sources for ${finding.title}`} className="mt-3 space-y-1.5">
                    {findingCitations.map((citation) => (
                      <li className="flex gap-2 text-xs leading-5 text-muted-foreground" data-slot="citation" key={citation.citationId}>
                        <BookOpen aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" strokeWidth={1.75} />
                        <span><span className="font-medium text-foreground">{citation.documentTitle}</span>{citationLocation(citation) && ` · ${citationLocation(citation)}`}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
