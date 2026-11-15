"""
Visual & Layout Diff Engine for SAP Print Workbench Bills.
Uses PyMuPDF (fitz) to render side-by-side pages, detect layout shifts,
apply dynamic masking for runtime spools and timestamps, and generate
color-coded bounding box overlays.
"""

import os
import re
from typing import Dict, Any, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont
import fitz  # PyMuPDF

from src.models import (
    VisualDiffResult, FieldDiff, DiffStatus, SpanishBillExtract
)


class VisualDiffEngine:
    """
    Compares baseline and target PDF invoices pixel-wise and coordinate-wise.
    Masks runtime metadata and highlights visual layout shifts.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.tolerances = self.config.get('tolerances', {})
        self.shift_tolerance = float(self.tolerances.get('layout_shift_tolerance_pt', 2.5))
        self.dynamic_patterns = self.config.get('dynamic_masking', {}).get('ignore_patterns', [
            r'(?i)spool[:\s_-]*\d+',
            r'(?i)fecha\s+impresi[óo]n[:\s]*\d{2}[\/\.-]\d{2}[\/\.-]\d{4}',
            r'(?i)hora\s+impresi[óo]n[:\s]*\d{2}:\d{2}(?::\d{2})?',
            r'(?i)impreso\s+por[:\s]*\w+',
            r'(?i)ads_session_id[:\s]*[a-zA-Z0-9_-]+',
            r'(?i)p[áa]gina\s+\d+\s+de\s+\d+'
        ])

    def is_dynamic_masked(self, text: str) -> bool:
        """Checks if text matches any dynamic runtime pattern to ignore."""
        for pattern in self.dynamic_patterns:
            if re.search(pattern, text):
                return True
        return False

    def compare_invoices(
        self,
        baseline_pdf_path: str,
        target_pdf_path: str,
        output_dir: str,
        invoice_number: str = "DIFF",
        mask_dynamic: bool = True
    ) -> List[VisualDiffResult]:
        """
        Compares all pages of baseline vs target PDF.
        Generates overlaid discrepancy images saved to output_dir.
        """
        os.makedirs(output_dir, exist_ok=True)
        results: List[VisualDiffResult] = []

        doc_base = fitz.open(baseline_pdf_path)
        doc_targ = fitz.open(target_pdf_path)

        num_pages = max(len(doc_base), len(doc_targ))

        try:
            for page_num in range(num_pages):
                page_idx = page_num + 1

                # If page missing in either
                if page_num >= len(doc_base):
                    results.append(VisualDiffResult(
                        invoice_number=invoice_number,
                        page_number=page_idx,
                        has_layout_shift=True,
                        field_diffs=[FieldDiff(
                            field_name="Page",
                            baseline_value="None",
                            target_value=f"Page {page_idx}",
                            status=DiffStatus.MISSING_BASELINE,
                            message="Page present in target but missing in baseline"
                        )]
                    ))
                    continue

                if page_num >= len(doc_targ):
                    results.append(VisualDiffResult(
                        invoice_number=invoice_number,
                        page_number=page_idx,
                        has_layout_shift=True,
                        field_diffs=[FieldDiff(
                            field_name="Page",
                            baseline_value=f"Page {page_idx}",
                            target_value="None",
                            status=DiffStatus.MISSING_TARGET,
                            message="Page missing in target"
                        )]
                    ))
                    continue

                page_b = doc_base[page_num]
                page_t = doc_targ[page_num]

                # Render page pixmaps (150 DPI)
                zoom = 150 / 72.0
                mat = fitz.Matrix(zoom, zoom)
                pix_b = page_b.get_pixmap(matrix=mat, alpha=False)
                pix_t = page_t.get_pixmap(matrix=mat, alpha=False)

                img_b_path = os.path.join(output_dir, f"{invoice_number}_p{page_idx}_baseline.png")
                img_t_path = os.path.join(output_dir, f"{invoice_number}_p{page_idx}_target.png")
                img_overlay_path = os.path.join(output_dir, f"{invoice_number}_p{page_idx}_diff_overlay.png")

                pix_b.save(img_b_path)
                pix_t.save(img_t_path)

                # Extract words [x0, y0, x1, y1, word, block_no, line_no, word_no]
                words_b = page_b.get_text("words")
                words_t = page_t.get_text("words")

                # Layout & text diff analysis
                field_diffs, has_shift, max_shift = self._analyze_words(
                    words_b, words_t, mask_dynamic
                )

                # Create overlay visual diff image
                self._create_overlay_image(
                    img_t_path, img_overlay_path, field_diffs, zoom
                )

                results.append(VisualDiffResult(
                    invoice_number=invoice_number,
                    page_number=page_idx,
                    has_layout_shift=has_shift,
                    max_shift_pt=max_shift,
                    field_diffs=field_diffs,
                    image_baseline_path=img_b_path,
                    image_target_path=img_t_path,
                    image_overlay_path=img_overlay_path
                ))

        finally:
            doc_base.close()
            doc_targ.close()

        return results

    def _analyze_words(
        self,
        words_b: List[Any],
        words_t: List[Any],
        mask_dynamic: bool
    ) -> Tuple[List[FieldDiff], bool, float]:
        """Compares word tokens and coordinates between baseline and target."""
        diffs: List[FieldDiff] = []
        has_shift = False
        max_shift = 0.0

        # Create lookup by index / near position
        len_b = len(words_b)
        len_t = len(words_t)
        min_len = min(len_b, len_t)

        for i in range(min_len):
            wb = words_b[i]
            wt = words_t[i]

            text_b = wb[4]
            text_t = wt[4]
            bbox_b = [wb[0], wb[1], wb[2], wb[3]]
            bbox_t = [wt[0], wt[1], wt[2], wt[3]]

            # Calculate coordinate shift
            dx = abs(bbox_t[0] - bbox_b[0])
            dy = abs(bbox_t[1] - bbox_b[1])
            shift = (dx**2 + dy**2) ** 0.5
            if shift > max_shift:
                max_shift = shift

            # Check dynamic masking
            is_dynamic = mask_dynamic and (self.is_dynamic_masked(text_b) or self.is_dynamic_masked(text_t))

            if is_dynamic:
                diffs.append(FieldDiff(
                    field_name=f"Word_{i}",
                    baseline_value=text_b,
                    target_value=text_t,
                    status=DiffStatus.MASKED,
                    message=f"Dynamic runtime field masked (shift: {shift:.1f}pt)",
                    bounding_box=bbox_t
                ))
            elif text_b != text_t:
                diffs.append(FieldDiff(
                    field_name=f"Word_{i}",
                    baseline_value=text_b,
                    target_value=text_t,
                    difference=f"'{text_b}' -> '{text_t}'",
                    tolerance_passed=False,
                    status=DiffStatus.DISCREPANCY,
                    message="Text content discrepancy",
                    bounding_box=bbox_t
                ))
            elif shift > self.shift_tolerance:
                has_shift = True
                diffs.append(FieldDiff(
                    field_name=f"Word_{i}",
                    baseline_value=text_b,
                    target_value=text_t,
                    difference=f"Shift: {shift:.1f}pt (dx={dx:.1f}, dy={dy:.1f})",
                    tolerance_passed=False,
                    status=DiffStatus.LAYOUT_SHIFT,
                    message=f"Layout position displacement ({shift:.1f}pt > {self.shift_tolerance}pt)",
                    bounding_box=bbox_t
                ))
            else:
                # Normal match (not included in discrepancy list to avoid noise, but recorded if needed)
                pass

        if len_t > len_b:
            for j in range(len_b, len_t):
                wt = words_t[j]
                diffs.append(FieldDiff(
                    field_name=f"Extra_Target_{j}",
                    baseline_value=None,
                    target_value=wt[4],
                    status=DiffStatus.DISCREPANCY,
                    message="Unexpected extra content in target",
                    bounding_box=[wt[0], wt[1], wt[2], wt[3]]
                ))

        return diffs, has_shift, max_shift

    def _create_overlay_image(
        self,
        base_img_path: str,
        output_path: str,
        diffs: List[FieldDiff],
        zoom: float
    ) -> None:
        """Draws bounding boxes over target page image."""
        img = Image.open(base_img_path).convert("RGBA")
        overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)

        # Colors
        color_red = (239, 68, 68, 80)        # Discrepancy / Defect fill
        border_red = (220, 38, 38, 240)
        color_amber = (245, 158, 11, 70)     # Masked dynamic fill
        border_amber = (217, 119, 6, 220)
        color_blue = (59, 130, 246, 70)      # Layout shift fill
        border_blue = (37, 99, 235, 240)

        for diff in diffs:
            if not diff.bounding_box:
                continue

            x0, y0, x1, y1 = diff.bounding_box
            # Scale coordinates by zoom
            px0 = int(x0 * zoom) - 2
            py0 = int(y0 * zoom) - 2
            px1 = int(x1 * zoom) + 2
            py1 = int(y1 * zoom) + 2

            if diff.status == DiffStatus.DISCREPANCY:
                draw.rectangle([px0, py0, px1, py1], fill=color_red, outline=border_red, width=2)
            elif diff.status == DiffStatus.LAYOUT_SHIFT:
                draw.rectangle([px0, py0, px1, py1], fill=color_blue, outline=border_blue, width=2)
            elif diff.status == DiffStatus.MASKED:
                draw.rectangle([px0, py0, px1, py1], fill=color_amber, outline=border_amber, width=2)

        # Blend
        result = Image.alpha_composite(img, overlay)
        result.convert("RGB").save(output_path, "PNG")
