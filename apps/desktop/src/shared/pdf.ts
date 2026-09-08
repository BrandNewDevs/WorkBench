import { z } from "zod";

export const pdfArtifactSchema = z.strictObject({
  artifactId: z.uuid(), fileName: z.string(), sha256: z.string(), sizeBytes: z.number(),
  pageCount: z.number(), validated: z.literal(true),
});
export const pdfDraftSchema = z.strictObject({
  title: z.string(), purpose: z.string(), header: z.string().nullable(), footer: z.string().nullable(),
  uncertaintyStatement: z.string().nullable(),
  sections: z.array(z.strictObject({heading:z.string(), paragraphs:z.array(z.string()),
    bullets:z.array(z.string()), table:z.array(z.array(z.string())), pageBreakBefore:z.boolean()})),
});
const region = {pageNumber:z.number(), x0:z.number(), y0:z.number(), x1:z.number(), y1:z.number()};
export const pdfEditSchema = z.strictObject({sourceId:z.uuid(), outputFileName:z.string(),
  operations:z.array(z.discriminatedUnion("operation", [
    z.strictObject({operation:z.literal("replaceText"), blockId:z.string(), replacement:z.string()}),
    z.strictObject({operation:z.literal("redactBlock"), blockId:z.string()}),
    z.strictObject({operation:z.literal("addAnnotation"), pageNumber:z.number(), text:z.string(), x:z.number(), y:z.number()}),
    z.strictObject({operation:z.literal("overlayText"), ...region, text:z.string(), fontSize:z.number()}),
    z.strictObject({operation:z.literal("redactRegion"), ...region}),
    z.strictObject({operation:z.literal("selectPages"), pages:z.array(z.number())}),
    z.strictObject({operation:z.literal("addPages"), position:z.enum(["before","after"]), draft:pdfDraftSchema}),
  ]))});
export const pdfApprovalSchema = z.strictObject({
  approvalId: z.uuid(), tool: z.enum(["create_pdf", "edit_pdf"]), argumentsHash: z.string(),
  status: z.enum(["pending", "executing", "rejected", "completed", "failed"]), fileName: z.string(),
  draft: pdfDraftSchema.nullable(), edit: pdfEditSchema.nullable(),
});
export const pdfTurnSchema = z.strictObject({
  requestId: z.uuid(), userMessage: z.string(), answer: z.string(),
  tool: z.enum(["summarize_pdf", "answer_pdf", "create_pdf", "edit_pdf"]), pages: z.array(z.number()),
  selectedModel: z.string(), usedFallback: z.boolean(), approval: pdfApprovalSchema.nullable(),
  artifact: pdfArtifactSchema.nullable(),
});
export const pdfViewSchema = z.strictObject({
  source: z.strictObject({uploadId: z.uuid(), sourceId: z.uuid(), fileName: z.string(),
    sha256: z.string(), mimeType: z.literal("application/pdf"), pageCount: z.number()}).nullable(),
  pages: z.array(z.object({pageNumber: z.number(), extractionMethod: z.enum(["native", "visual", "mixed"]),
    textBlocks: z.array(z.object({blockId:z.string(), text:z.string(), pageNumber:z.number()}))})),
  turns: z.array(pdfTurnSchema),
  activity: z.array(z.string()),
});
export const turnStreamRequestSchema = z.strictObject({
  mode: z.enum(["localConversation", "pdfDocument"]), sessionId: z.uuid(),
  message: z.string().trim().min(1).max(20_000), clientRequestId: z.uuid(), uploadId: z.uuid().optional(),
});
export const turnStreamEventSchema = z.strictObject({
  event: z.enum(["turn.accepted", "tool.started", "tool.progress", "tool.completed",
    "assistant.delta", "assistant.reset", "assistant.completed", "turn.failed"]),
  text: z.string(), result: z.json().nullable(),
});
export type PdfTurn = z.infer<typeof pdfTurnSchema>;
export type PdfView = z.infer<typeof pdfViewSchema>;
export type TurnStreamRequest = z.infer<typeof turnStreamRequestSchema>;
export type TurnStreamEvent = z.infer<typeof turnStreamEventSchema>;
