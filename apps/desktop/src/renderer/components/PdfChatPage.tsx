import { useEffect, useRef, useState } from "react";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";
import { localApi } from "../api/localApi";
import { streamTurn } from "../api/turnStream";
import { pdfTurnSchema, pdfViewSchema, type PdfView } from "../../shared/pdf";
import type { ChatSession, LocalServiceRequest } from "../../shared/contracts";
import { pendingPdfTurn, type PendingPdfTurn } from "../lib/pdfTurns";

async function pdfRequest(request: LocalServiceRequest): Promise<unknown> {
  const response = await window.workbench.requestLocalService(request);
  if (response.status < 200 || response.status >= 300) throw new Error("PDF request failed. Check the document and local models.");
  return JSON.parse(response.body) as unknown;
}

export function PdfChatPage({ onLocalChat }: { onLocalChat: () => void }) {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionId, setSessionId] = useState<string>();
  const [view, setView] = useState<PdfView>({source: null, pages: [], turns: [], activity: []});
  const [uploadId, setUploadId] = useState<string>();
  const [filename, setFilename] = useState("");
  const [draft, setDraft] = useState("");
  const draftRef = useRef("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [activity, setActivity] = useState<string[]>([]);
  const [provisional, setProvisional] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const retryRequestRef = useRef<PendingPdfTurn | null>(null);
  useEffect(() => {
    void localApi.listChatSessions().then(result => setSessions(result.sessions.filter(s => s.workflowType === "pdfDocument")));
    return () => abortRef.current?.abort();
  }, []);
  const changeDraft = (text: string) => { draftRef.current = text; setDraft(text); };
  const load = async (id: string) => {
    const restored = pdfViewSchema.parse(await pdfRequest({operation: "pdfView", sessionId: id}));
    setSessionId(id); setView(restored); setUploadId(restored.source?.uploadId); setFilename(restored.source?.fileName ?? "");
  };
  const ensureSession = async () => {
    if (sessionId) return sessionId;
    const session = await localApi.createChatSession({workflowType: "pdfDocument", title: "PDF conversation", clientSessionId: crypto.randomUUID()});
    setSessionId(session.sessionId); setSessions(old => [session, ...old]);
    return session.sessionId;
  };
  const attach = async () => {
    if (busy || uploadId) return;
    setBusy(true); setError("");
    try {
      const selected = await window.workbench.selectUploadFiles("inspectionReport");
      if (selected.kind !== "selected") return;
      const id = await ensureSession();
      const uploaded = await localApi.uploadWorkflowFile(id, selected.file.uploadToken);
      setUploadId(uploaded.uploadId); setFilename(uploaded.fileName); await load(id);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "PDF upload failed"); }
    finally { setBusy(false); }
  };
  const send = async () => {
    if (busy || !draft.trim()) return;
    const submitted = draft;
    const abort = new AbortController(); abortRef.current = abort;
    const pendingRequest = pendingPdfTurn(
      retryRequestRef.current,
      submitted,
      () => crypto.randomUUID(),
    );
    const requestId = pendingRequest.requestId;
    retryRequestRef.current = pendingRequest;
    setBusy(true); setError(""); setActivity([]); setProvisional("");
    try {
      const id = await ensureSession();
      const result = await streamTurn({mode: "pdfDocument", sessionId: id, message: submitted,
        clientRequestId: requestId, ...(uploadId ? {uploadId} : {})}, event => {
        if (event.event === "turn.accepted" && draftRef.current === submitted) changeDraft("");
        if (event.event.startsWith("tool.")) setActivity(old => [...old, event.text]);
        if (event.event === "assistant.reset") setProvisional("");
        if (event.event === "assistant.delta") setProvisional(old => old + event.text);
      }, abort.signal);
      pdfTurnSchema.parse(result);
      await load(id);
      retryRequestRef.current = null;
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "PDF generation failed");
      if (!draftRef.current) changeDraft(submitted);
    } finally { setBusy(false); setProvisional(""); abortRef.current = null; }
  };
  const approve = async (approvalId: string, argumentsHash: string, approved: boolean) => {
    if (!sessionId || busy) return;
    setBusy(true); setError("");
    try {
      await pdfRequest({operation: "pdfApprove", sessionId, approvalId, argumentsHash, approve: approved});
      await load(sessionId);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "PDF export failed"); }
    finally { setBusy(false); }
  };
  return <section className="flex min-h-0 flex-1 flex-col gap-4 p-8">
    <header className="flex items-center gap-3"><h1 className="text-lg font-medium">PDF</h1>
      <Button variant="outline" onClick={onLocalChat} disabled={busy}>Local chat</Button>
      <Button variant="outline" disabled={busy} onClick={() => {setSessionId(undefined); setView({source:null,pages:[],turns:[],activity:[]}); setUploadId(undefined); setFilename(""); setActivity([]); retryRequestRef.current=null;}}>New PDF conversation</Button>
    </header>
    <p className="text-xs text-muted-foreground">Offline — local models only. Create/edit actions require approval.</p>
    <nav className="flex flex-wrap gap-2" aria-label="Recent PDF conversations">{sessions.map(s => <Button key={s.sessionId} disabled={busy} variant="ghost" onClick={() => {retryRequestRef.current=null; void load(s.sessionId).catch(() => setError("Could not restore PDF conversation"));}}>{s.title}</Button>)}</nav>
    <div className="min-h-0 flex-1 space-y-4 overflow-y-auto">
      {filename && <div className="rounded border p-3">{filename} · {view.source?.pageCount ?? "…"} pages
        {view.pages.length > 0 && <span> · {view.pages.every(p => p.extractionMethod === "native") ? "Native text" : view.pages.every(p => p.extractionMethod === "visual") ? "Scanned" : "Mixed"}</span>}</div>}
      {view.turns.map(turn => <article key={turn.requestId} className="space-y-3 rounded border p-4">
        <p className="font-medium">{turn.userMessage}</p><p className="text-xs text-muted-foreground">{turn.tool} · {turn.selectedModel}{turn.usedFallback ? " · fallback" : ""}</p>
        <p className="whitespace-pre-wrap">{turn.answer}</p>
        <div className="flex gap-2">{turn.pages.map(page => <span className="rounded bg-muted px-2 text-xs" key={page}>Page {page}</span>)}</div>
        {turn.approval && <div className="rounded border p-3">
          <p>{turn.approval.tool} · {turn.approval.fileName} · {turn.approval.status}</p>
          <details><summary>Review proposed document / edits</summary>
            <p className="text-sm">Approval creates a new local PDF; the uploaded original is unchanged.</p>
            <details><summary>Complete approved plan (including tables and layout)</summary><pre className="overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(turn.approval.draft ?? turn.approval.edit, null, 2)}</pre></details>
            {turn.approval.draft && <div className="space-y-2 py-3">
              <h3 className="font-medium">{turn.approval.draft.title}</h3><p>{turn.approval.draft.purpose}</p>
              {turn.approval.draft.sections.map((section,i) => <div key={i}><h4 className="font-medium">{section.heading}</h4>{section.paragraphs.map((p,j) => <p key={j}>{p}</p>)}</div>)}
            </div>}
            {turn.approval.edit?.operations.map((op,i) => {
              const block = "blockId" in op ? view.pages.flatMap(p => p.textBlocks).find(b => b.blockId === op.blockId) : undefined;
              const description = op.operation === "replaceText" ? `Page ${block?.pageNumber}: replace “${block?.text}” with “${op.replacement}”`
                : op.operation === "redactBlock" ? `Page ${block?.pageNumber}: redact “${block?.text}”`
                : op.operation === "addAnnotation" ? `Page ${op.pageNumber}: add note “${op.text}”`
                : op.operation === "overlayText" ? `Page ${op.pageNumber}: overlay “${op.text}” in the proposed region`
                : op.operation === "redactRegion" ? `Page ${op.pageNumber}: redact region (${op.x0}, ${op.y0}) to (${op.x1}, ${op.y1})`
                : op.operation === "selectPages" ? `Create copy using pages in this order: ${op.pages.join(", ")}`
                : `Add “${op.draft.title}” pages ${op.position} the original`;
              return <p key={i} className="py-2 text-sm">{description}</p>;
            })}
          </details>
          {turn.approval.status === "pending" && <div className="mt-3 flex gap-2"><Button disabled={busy} onClick={() => void approve(turn.approval!.approvalId, turn.approval!.argumentsHash, true)}>Approve creation of new PDF</Button><Button disabled={busy} variant="outline" onClick={() => void approve(turn.approval!.approvalId, turn.approval!.argumentsHash, false)}>Reject</Button></div>}
        </div>}
        {turn.artifact && <div className="flex gap-2"><span>{turn.artifact.fileName} · {turn.artifact.pageCount} pages</span>{(["open", "save"] as const).map(action => <Button key={action} variant="outline" onClick={() => void pdfRequest({operation:"pdfArtifact", sessionId:sessionId!, artifactId:turn.artifact!.artifactId, action}).catch(() => setError("Could not open or save PDF"))}>{action === "open" ? "Open" : "Save as…"}</Button>)}</div>}
      </article>)}
      <div role="status" className="text-sm text-muted-foreground">{(busy ? activity : view.activity).map((text,i) => <p key={i}>{text}</p>)}</div>
      {busy && <p className="whitespace-pre-wrap">{provisional || "Working locally…"}</p>}
      {error && <p role="alert" className="text-destructive">{error}</p>}
    </div>
    <div className="space-y-2"><Textarea aria-label="PDF message" value={draft} onChange={e => changeDraft(e.target.value)} onKeyDown={e => {if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {e.preventDefault(); void send();}}} placeholder="Summarize, ask a question, create a PDF, or describe an edit…" />
      <div className="flex gap-2"><Button disabled={busy || !!uploadId} variant="outline" onClick={() => void attach()}>Attach PDF</Button><Button disabled={busy || !draft.trim()} onClick={() => void send()}>Send</Button>{busy && <Button variant="outline" onClick={() => abortRef.current?.abort()}>Cancel</Button>}</div>
    </div>
  </section>;
}
