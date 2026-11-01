"""PDF inspection reports (layout follows cut-mark-station) and file-based report lookup.

Folder structure (same scheme as cut-mark-station: date-wise, then verdict-wise):
    <report directory>/<YYYY>/<MM>/<DD>/<GO|NO_GO>/<PART>_<HHMMSS>.pdf
Listing and lookup by part number scan this tree — no database.
"""
from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image as RLImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from src.core import config

TITLE = "CAMSHAFT 2D MATRIX & THREAD INSPECTION REPORT"
BLUE = colors.HexColor("#0f766e")  # teal, matches the UI accent
UNREAD = "UNREAD"
_NAME_RE = re.compile(r"^(?P<part>.+)_(?P<time>\d{6})(?:_(?P<n>\d+))?\.pdf$")
VERDICTS = ("GO", "NO_GO")
_STATE_TEXT = {"PRESENT": "THREAD PRESENT", "ABSENT": "THREAD ABSENT", "NOT_VISIBLE": "THREAD NOT VISIBLE"}


def _safe_part(part: Optional[str]) -> str:
    if not part:
        return UNREAD
    return re.sub(r"[^A-Za-z0-9\-]", "-", part)[:80] or UNREAD


def _logo(path: Path, max_h: float, max_w: float):
    if not path.exists():
        return ""
    img = RLImage(str(path))
    aspect = img.imageWidth / img.imageHeight if img.imageHeight else 1.0
    h, w = max_h, max_h * aspect
    if w > max_w:
        w, h = max_w, max_w / aspect
    img.drawWidth, img.drawHeight = w, h
    return img


def _image_flowable(image: Optional[np.ndarray], tmpdir: str, name: str, width: float):
    if image is None:
        return None
    h, w = image.shape[:2]
    if w > 1000:
        image = cv2.resize(image, (1000, int(h * 1000 / w)), interpolation=cv2.INTER_AREA)
        h, w = image.shape[:2]
    path = os.path.join(tmpdir, name)
    cv2.imwrite(path, image, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return RLImage(path, width=width, height=width * h / w)


class ReportWriter:
    """``root_fn`` returns the current report directory (changeable from SETUP at runtime)."""

    def __init__(self, root_fn=None):
        self._root_fn = root_fn or (lambda: config.PDF_DIR)

    @property
    def root(self) -> Path:
        return Path(self._root_fn())

    # ------------------------------------------------------------ writing
    def write(self, *, timestamp: datetime, operator: str, mode: str, overall: str,
              decoded_number: Optional[str], code_result: dict, thread_result: dict,
              code_image: Optional[np.ndarray], thread_image: Optional[np.ndarray]) -> str:
        folder = self.root / timestamp.strftime("%Y") / timestamp.strftime("%m") / timestamp.strftime("%d") / overall
        folder.mkdir(parents=True, exist_ok=True)
        base = f"{_safe_part(decoded_number)}_{timestamp.strftime('%H%M%S')}"
        path, n = folder / f"{base}.pdf", 1
        while path.exists():
            n += 1
            path = folder / f"{base}_{n}.pdf"

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle("T", parent=styles["Heading1"], fontSize=15, textColor=BLUE,
                                     alignment=TA_CENTER, leading=19, spaceAfter=0, spaceBefore=0)
        heading = ParagraphStyle("H", parent=styles["Heading2"], fontSize=11, textColor=BLUE,
                                 spaceAfter=4, alignment=TA_LEFT)
        verdict_style = ParagraphStyle("V", parent=styles["Heading1"], fontSize=22, alignment=TA_CENTER,
                                       textColor=colors.HexColor("#16a34a" if overall == "GO" else "#dc2626"))
        story = []
        header = Table([[_logo(config.ASSETS_DIR / "logo.jpeg", 0.7 * inch, 1.3 * inch),
                         Paragraph(TITLE, title_style),
                         ""]],  # right cell kept empty so the title stays centred (no client logo)
                       colWidths=[1.5 * inch, 4.3 * inch, 1.5 * inch])
        header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (0, 0), "LEFT"),
                                    ("ALIGN", (1, 0), (1, 0), "CENTER"), ("ALIGN", (2, 0), (2, 0), "RIGHT"),
                                    ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
        story += [header, Spacer(1, 0.15 * inch)]

        info = Table([
            ["PART NUMBER (2D):", decoded_number or UNREAD, "VERDICT:", "GO" if overall == "GO" else "NO-GO"],
            ["OPERATOR:", operator or "-", "MODE:", mode.upper()],
            ["DATE:", timestamp.strftime("%Y-%m-%d %H:%M:%S"), "", ""],
        ], colWidths=[1.45 * inch, 2.45 * inch, 0.95 * inch, 2.45 * inch], hAlign="CENTER")
        info.setStyle(TableStyle([("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                                  ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                                  ("FONTSIZE", (0, 0), (-1, -1), 9),
                                  ("TEXTCOLOR", (0, 0), (0, -1), BLUE), ("TEXTCOLOR", (2, 0), (2, -1), BLUE)]))
        story += [info, Spacer(1, 0.1 * inch), Paragraph("GO" if overall == "GO" else "NO-GO", verdict_style),
                  Spacer(1, 0.1 * inch)]

        story.append(Paragraph("CHECK RESULTS", heading))
        small = ParagraphStyle("S", parent=styles["BodyText"], fontSize=8.5, leading=10, wordWrap="CJK")
        rows = [
            ["Check", "Result", "Status", "Detail"],
            ["2D Data Matrix", Paragraph(code_result.get("text") or "NOT READ", small),
             "PASS" if code_result.get("passed") else "FAIL",
             Paragraph(code_result.get("message") or "", styles["BodyText"])],
            ["Thread presence", Paragraph(_STATE_TEXT.get(thread_result.get("state"), str(thread_result.get("state"))), small),
             "PASS" if thread_result.get("passed") else "FAIL",
             Paragraph(thread_result.get("message") or "", styles["BodyText"])],
        ]
        table = Table(rows, colWidths=[1.2 * inch, 2.3 * inch, 0.6 * inch, 3.2 * inch])
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("BACKGROUND", (0, 0), (-1, 0), BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TEXTCOLOR", (2, 1), (2, 1), colors.HexColor("#16a34a" if code_result.get("passed") else "#dc2626")),
            ("TEXTCOLOR", (2, 2), (2, 2), colors.HexColor("#16a34a" if thread_result.get("passed") else "#dc2626")),
        ]))
        story += [table, Spacer(1, 0.15 * inch)]

        with tempfile.TemporaryDirectory() as tmp:
            images = [(_image_flowable(code_image, tmp, "code.jpg", 3.55 * inch), "2D CODE CAMERA"),
                      (_image_flowable(thread_image, tmp, "thread.jpg", 3.55 * inch), "THREAD CAMERA")]
            images = [(img, label) for img, label in images if img is not None]
            if images:
                story.append(Paragraph("INSPECTION IMAGES", heading))
                grid = Table([[label for _, label in images], [img for img, _ in images]],
                             colWidths=[3.65 * inch] * len(images))
                grid.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                                          ("FONTSIZE", (0, 0), (-1, 0), 8), ("TEXTCOLOR", (0, 0), (-1, 0), BLUE),
                                          ("ALIGN", (0, 0), (-1, -1), "CENTER")]))
                story += [grid, Spacer(1, 0.1 * inch)]
            SimpleDocTemplate(str(path), pagesize=A4, topMargin=0.5 * inch, bottomMargin=0.5 * inch,
                              leftMargin=0.5 * inch, rightMargin=0.5 * inch,
                              title=TITLE, author="CamShaft Inspection Station").build(story)
        return self.relative_id(path)

    # ------------------------------------------------------------ lookup
    def relative_id(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    def resolve(self, report_id: str) -> Path:
        candidate = (self.root / report_id).resolve()
        if self.root.resolve() not in candidate.parents or candidate.suffix.lower() != ".pdf" or not candidate.is_file():
            raise FileNotFoundError(report_id)
        return candidate

    @staticmethod
    def _record(report_id: str, year: str, month: str, day: str, verdict: str, match: re.Match) -> dict:
        t = match["time"]
        ts = datetime(int(year), int(month), int(day), int(t[:2]), int(t[2:4]), int(t[4:]))
        part = match["part"]
        return {"report_id": report_id, "timestamp": ts.isoformat(),
                "decoded_number": None if part == UNREAD else part,
                "overall_result": verdict, "report_url": f"/api/reports/file/{report_id}"}

    def _iter(self):
        """Newest first: walk YYYY/MM/DD (descending), merge GO and NO_GO by time."""
        root = self.root
        if not root.exists():
            return
        digits = lambda p: p.is_dir() and p.name.isdigit()  # noqa: E731
        for year in sorted(filter(digits, root.iterdir()), key=lambda p: p.name, reverse=True):
            for month in sorted(filter(digits, year.iterdir()), key=lambda p: p.name, reverse=True):
                for day in sorted(filter(digits, month.iterdir()), key=lambda p: p.name, reverse=True):
                    records = []
                    for verdict in VERDICTS:
                        folder = day / verdict
                        if not folder.is_dir():
                            continue
                        for pdf in folder.glob("*.pdf"):
                            match = _NAME_RE.match(pdf.name)
                            if match:
                                rid = f"{year.name}/{month.name}/{day.name}/{verdict}/{pdf.name}"
                                records.append(self._record(rid, year.name, month.name, day.name, verdict, match))
                    records.sort(key=lambda r: (r["timestamp"], r["report_id"]), reverse=True)
                    yield from records

    def recent(self, limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        out = []
        for record in self._iter():
            out.append(record)
            if len(out) >= limit:
                break
        return out

    def by_part(self, part: str) -> list[dict]:
        wanted = _safe_part(part.strip())
        return [r for r in self._iter() if (r["decoded_number"] or UNREAD) == wanted]
