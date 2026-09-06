"""Regenerate the synthetic image-only golden inspection report."""

from io import BytesIO
from pathlib import Path

import pymupdf

OUTPUT = Path("tests/fixtures/golden/scanned-inspection-report.pdf")
PAGE = pymupdf.Rect(0, 0, 595, 842)


def _page(document: pymupdf.Document, title: str, body: str, page_number: int) -> None:
    page = document.new_page(width=PAGE.width, height=PAGE.height)
    page.draw_rect(pymupdf.Rect(42, 42, 553, 800), color=(0.22, 0.3, 0.38), width=1)
    page.insert_text((58, 82), "WORKBENCH SYNTHETIC INSPECTION RECORD", fontsize=9)
    page.insert_text((58, 126), title, fontsize=18)
    page.insert_textbox(
        pymupdf.Rect(58, 158, 537, 705),
        body,
        fontsize=10,
        lineheight=1.25,
    )
    page.insert_text((58, 775), f"Synthetic fixture | Page {page_number} of 2", fontsize=9)


def build() -> None:
    """Write a two-page PDF whose pages contain raster images only."""

    source = pymupdf.open()
    _page(
        source,
        "Pump P-17 Routine Visual Inspection",
        "Record ID: WB-GOLDEN-INSPECTION-001\n\n"
        "Location: Fictional Maintenance Bay\n"
        "Equipment: Generic centrifugal pump P-17\n"
        "Inspection type: Routine visual inspection\n"
        "Status: Draft observations - engineering verification pending\n\n"
        "This document is synthetic and contains no operational or organization data.\n\n"
        "Detailed observations are recorded on page 2.",
        1,
    )
    _page(
        source,
        "Observed Conditions",
        "1. LOWER OUTLET FLANGE\n"
        "Localized orange surface corrosion was observed around the lower outlet flange. "
        "Remaining wall thickness was not available from the visual inspection.\n\n"
        "2. SHAFT SEAL\n"
        "A small dark oil seepage stain was observed directly below the shaft seal. "
        "Leak rate was not measured during this inspection.\n\n"
        "3. COUPLING SAFETY GUARD\n"
        "One attachment bolt was missing from the yellow coupling safety guard. "
        "The remaining visible fasteners were installed.\n\n"
        "Required follow-up: Apply the approved pump maintenance SOP. Do not infer operating "
        "safety, wall thickness, leak rate, or repair approval from this visual record alone.",
        2,
    )

    scanned = pymupdf.open()
    for source_page in source:
        pixmap = source_page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
        image = pixmap.tobytes("png")
        page = scanned.new_page(width=PAGE.width, height=PAGE.height)
        page.insert_image(PAGE, stream=image)
    scanned.set_metadata(
        {
            "title": "Synthetic Pump Inspection Record",
            "author": "WorkBench golden corpus",
            "subject": "Sanitized image-only evaluation fixture",
        }
    )
    output = BytesIO()
    scanned.save(output, garbage=4, deflate=True)
    OUTPUT.write_bytes(output.getvalue())
    scanned.close()
    source.close()


if __name__ == "__main__":
    build()
