"""Deterministic local ReportLab PDF renderer for approved drafts."""

from pathlib import Path

from reportlab.lib import colors  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # type: ignore[import-untyped]
from reportlab.lib.units import mm  # type: ignore[import-untyped]
from reportlab.platypus import (  # type: ignore[import-untyped]
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.pdf.contracts import PdfDocumentDraft


class PdfRenderError(ValueError):
    """A controlled local rendering error."""


class LocalPdfRenderer:
    """Render a professional offline PDF without browser or cloud dependencies."""

    def render_draft(self, draft: PdfDocumentDraft, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        styles = getSampleStyleSheet()
        title = ParagraphStyle(
            "WorkBenchTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=19,
            leading=23,
            spaceAfter=10,
        )
        heading = ParagraphStyle(
            "WorkBenchHeading",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=colors.HexColor("#17324D"),
            spaceBefore=10,
            spaceAfter=5,
        )
        body = ParagraphStyle(
            "WorkBenchBody",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=14,
            spaceAfter=7,
        )
        story = [Paragraph(draft.title, title), Paragraph(draft.purpose, body), Spacer(1, 4)]
        for section in draft.sections:
            story.append(Paragraph(section.heading, heading))
            story.extend(Paragraph(paragraph, body) for paragraph in section.paragraphs)
            if section.bullets:
                story.append(
                    ListFlowable(
                        [ListItem(Paragraph(item, body)) for item in section.bullets],
                        bulletType="bullet",
                        leftIndent=15,
                    )
                )
            if section.table:
                table = Table(section.table, repeatRows=1, hAlign="LEFT")
                table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17324D")),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                            ("FONTSIZE", (0, 0), (-1, -1), 8),
                            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E1")),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 5),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                            ("TOPPADDING", (0, 0), (-1, -1), 4),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        ]
                    )
                )
                story.extend([Spacer(1, 4), table])
        if draft.uncertainty_statement:
            story.extend(
                [
                    Spacer(1, 8),
                    Paragraph("Uncertainty", heading),
                    Paragraph(draft.uncertainty_statement, body),
                ]
            )

        def decorate(canvas, document) -> None:  # type: ignore[no-untyped-def]
            canvas.saveState()
            if draft.header:
                canvas.setFont("Helvetica", 8)
                canvas.setFillColor(colors.HexColor("#667085"))
                canvas.drawString(document.leftMargin, A4[1] - 13 * mm, draft.header)
            footer_text = draft.footer or "WorkBench local draft"
            canvas.setFont("Helvetica", 8)
            canvas.setFillColor(colors.HexColor("#667085"))
            canvas.drawCentredString(A4[0] / 2, 12 * mm, f"{footer_text} - Page {document.page}")
            canvas.restoreState()

        try:
            document = SimpleDocTemplate(
                str(destination),
                pagesize=A4,
                leftMargin=20 * mm,
                rightMargin=20 * mm,
                topMargin=24 * mm,
                bottomMargin=20 * mm,
                title=draft.title,
            )
            document.build(story, onFirstPage=decorate, onLaterPages=decorate)
        except (OSError, ValueError, TypeError) as error:
            destination.unlink(missing_ok=True)
            raise PdfRenderError(
                "local PDF renderer could not create the requested draft"
            ) from error
