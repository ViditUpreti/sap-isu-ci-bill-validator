"""
Audit & Defect Report Generator for SAP IS-U & CI Billing Validation.
Generates:
1. Multi-tab, conditionally-formatted Excel workbook (openpyxl) with JIRA/QMetry defect tracking
2. Self-contained, responsive Executive HTML Sign-off report with KPI cards and charts
"""

import os
from decimal import Decimal
from datetime import datetime
from typing import List, Dict, Any, Optional

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from src.models import (
    BatchReconciliationSummary, ReconciliationRecord, ReconcileStatus,
    MathValidationResult
)
from utils.locale_parser import format_spanish_currency


class ReportGenerator:
    """Produces executive and defect reports for SAP IS-U SIT/UAT validation batches."""

    def __init__(self, output_dir: str = "reports"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def generate_excel_report(
        self,
        summary: BatchReconciliationSummary,
        math_results: Optional[List[MathValidationResult]] = None,
        filename: Optional[str] = None
    ) -> str:
        """Creates a formatted multi-sheet Excel audit report ready for JIRA / QMetry."""
        if not filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"SAP_Bill_Validation_Audit_{ts}.xlsx"

        file_path = os.path.join(self.output_dir, filename)
        wb = openpyxl.Workbook()
        wb.remove(wb.active)  # Remove default sheet

        # Styles
        font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        fill_header = PatternFill(start_color="1B365D", end_color="1B365D", fill_type="solid")  # Navy SAP Blue
        font_title = Font(name="Calibri", size=16, bold=True, color="1B365D")
        font_subtitle = Font(name="Calibri", size=11, italic=True, color="555555")

        fill_pass = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
        font_pass = Font(name="Calibri", size=11, color="006100", bold=True)
        fill_fail = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
        font_fail = Font(name="Calibri", size=11, color="9C0006", bold=True)
        fill_outsort = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
        font_outsort = Font(name="Calibri", size=11, color="9C6500", bold=True)

        thin_border = Border(
            left=Side(style='thin', color='DDDDDD'),
            right=Side(style='thin', color='DDDDDD'),
            top=Side(style='thin', color='DDDDDD'),
            bottom=Side(style='thin', color='DDDDDD')
        )

        # ----------------------------------------------------------------------
        # SHEET 1: Executive Summary
        # ----------------------------------------------------------------------
        ws1 = wb.create_sheet(title="Executive Summary")
        ws1.views.sheetView[0].showGridLines = True

        ws1.merge_cells("A1:G1")
        ws1["A1"] = "SAP IS-U & CI Automated Bill Print & Invoice Validation Suite"
        ws1["A1"].font = font_title

        ws1.merge_cells("A2:G2")
        ws1["A2"] = f"Spain Utility Edition — SIT/UAT Regression & Reconciliation Sign-off | Generated: {summary.timestamp}"
        ws1["A2"].font = font_subtitle

        # KPI Metrics Cards
        kpi_titles = ["Total Invoices Tested", "Pass Rate (%)", "Outsorted Invoices", "Failed Reconciliations", "Total Financial Variance"]
        kpi_vals = [
            summary.total_invoices_tested,
            f"{summary.pass_rate_pct:.1f}%",
            summary.outsorted_count,
            summary.failed_count,
            format_spanish_currency(summary.total_financial_variance_eur)
        ]

        ws1.append([])
        for col_idx, (title, val) in enumerate(zip(kpi_titles, kpi_vals), start=1):
            cell_title = ws1.cell(row=4, column=col_idx, value=title)
            cell_title.font = Font(name="Calibri", size=10, bold=True, color="555555")
            cell_title.fill = PatternFill(start_color="F2F4F8", end_color="F2F4F8", fill_type="solid")
            cell_title.alignment = Alignment(horizontal="center")
            cell_title.border = thin_border

            cell_val = ws1.cell(row=5, column=col_idx, value=val)
            cell_val.font = Font(name="Calibri", size=14, bold=True, color="1B365D")
            cell_val.alignment = Alignment(horizontal="center")
            cell_val.border = thin_border

        # Tariff Breakdown Table
        ws1.cell(row=7, column=1, value="Tariff Breakdown Summary").font = Font(name="Calibri", size=13, bold=True, color="1B365D")
        headers_tariff = ["Tariff", "Total Count", "Passed", "Outsorted", "Failed", "Pass Rate (%)"]
        ws1.append([])
        row_num = 9
        for col_idx, h in enumerate(headers_tariff, start=1):
            cell = ws1.cell(row=row_num, column=col_idx, value=h)
            cell.font = font_header
            cell.fill = fill_header
            cell.alignment = Alignment(horizontal="center")

        for tariff, data in summary.tariff_breakdown.items():
            row_num += 1
            tot = data.get("total", 0)
            passed = data.get("pass", 0)
            out = data.get("outsorted", 0)
            failed = data.get("fail", 0)
            rate = f"{(passed / tot * 100):.1f}%" if tot > 0 else "0.0%"
            row_vals = [tariff, tot, passed, out, failed, rate]
            for col_idx, val in enumerate(row_vals, start=1):
                cell = ws1.cell(row=row_num, column=col_idx, value=val)
                cell.border = thin_border
                cell.alignment = Alignment(horizontal="center" if col_idx > 1 else "left")

        # ----------------------------------------------------------------------
        # SHEET 2: Reconciliation Details
        # ----------------------------------------------------------------------
        ws2 = wb.create_sheet(title="3-Way Reconciliation Matrix")
        ws2.views.sheetView[0].showGridLines = True

        recon_headers = [
            "Invoice No (OPBEL)", "CUPS", "Contract Account", "Tariff",
            "Bill Total (€)", "ERDK Total (€)", "CI Doc H (€)", "DFKKOP Open Item (€)",
            "Var vs ERDK (€)", "Var vs DFKKOP (€)", "Math Valid", "Status", "Outsorted", "Defect Details"
        ]

        for col_idx, h in enumerate(recon_headers, start=1):
            cell = ws2.cell(row=1, column=col_idx, value=h)
            cell.font = font_header
            cell.fill = fill_header
            cell.alignment = Alignment(horizontal="center")

        for row_idx, r in enumerate(summary.records, start=2):
            vals = [
                r.invoice_number,
                r.cups,
                r.contract_account or "N/A",
                r.tariff,
                float(r.bill_total),
                float(r.erdk_total) if r.erdk_total is not None else "N/A",
                float(r.inv_doc_h_total) if r.inv_doc_h_total is not None else "N/A",
                float(r.dfkkop_total) if r.dfkkop_total is not None else "N/A",
                float(r.var_bill_vs_erdk) if r.var_bill_vs_erdk is not None else 0.0,
                float(r.var_bill_vs_dfkkop) if r.var_bill_vs_dfkkop is not None else 0.0,
                "YES" if r.math_valid else "NO",
                r.overall_status.value,
                "YES" if r.is_outsorted else "NO",
                "; ".join(r.defect_details) if r.defect_details else "None"
            ]

            for col_idx, v in enumerate(vals, start=1):
                cell = ws2.cell(row=row_idx, column=col_idx, value=v)
                cell.border = thin_border
                if col_idx in (5, 6, 7, 8, 9, 10) and isinstance(v, float):
                    cell.number_format = '#,##0.00 €'
                    cell.alignment = Alignment(horizontal="right")
                else:
                    cell.alignment = Alignment(horizontal="left")

                # Format status column
                if col_idx == 12:
                    if v == "PASS":
                        cell.fill = fill_pass
                        cell.font = font_pass
                    elif v == "OUTSORTED":
                        cell.fill = fill_outsort
                        cell.font = font_outsort
                    else:
                        cell.fill = fill_fail
                        cell.font = font_fail

        # ----------------------------------------------------------------------
        # SHEET 3: JIRA / QMetry Defect Log
        # ----------------------------------------------------------------------
        ws3 = wb.create_sheet(title="JIRA & QMetry Defects")
        ws3.views.sheetView[0].showGridLines = True

        defect_headers = [
            "Defect Key", "Summary", "Severity", "Component", "Tariff",
            "Invoice No", "Expected Value", "Actual Extracted", "Variance (€)",
            "Outsorted", "Steps to Reproduce / Defect Description"
        ]

        for col_idx, h in enumerate(defect_headers, start=1):
            cell = ws3.cell(row=1, column=col_idx, value=h)
            cell.font = font_header
            cell.fill = fill_header
            cell.alignment = Alignment(horizontal="center")

        bug_counter = 1
        curr_row = 2
        for r in summary.records:
            if r.overall_status != ReconcileStatus.PASS or r.defect_details:
                for defect in r.defect_details:
                    key = f"ISU-DEF-{bug_counter:03d}"
                    severity = "Critical" if r.is_outsorted else "Major"
                    component = "Print Workbench / ADS"
                    if "ERDK" in defect:
                        component = "SAP ERDK Print Doc"
                    elif "DFKKOP" in defect:
                        component = "FI-CA Posting (DFKKOP)"
                    elif "DFKKINV" in defect:
                        component = "CI Invoicing (DFKKINV)"

                    ws3.cell(row=curr_row, column=1, value=key).alignment = Alignment(horizontal="center")
                    ws3.cell(row=curr_row, column=2, value=f"[SAP-ES Bill Print] {defect[:80]}")
                    cell_sev = ws3.cell(row=curr_row, column=3, value=severity)
                    cell_sev.alignment = Alignment(horizontal="center")
                    if severity == "Critical":
                        cell_sev.fill = fill_fail
                        cell_sev.font = font_fail
                    else:
                        cell_sev.fill = fill_outsort
                        cell_sev.font = font_outsort

                    ws3.cell(row=curr_row, column=4, value=component)
                    ws3.cell(row=curr_row, column=5, value=r.tariff).alignment = Alignment(horizontal="center")
                    ws3.cell(row=curr_row, column=6, value=r.invoice_number).alignment = Alignment(horizontal="center")
                    ws3.cell(row=curr_row, column=7, value=float(r.erdk_total or r.bill_total))
                    ws3.cell(row=curr_row, column=8, value=float(r.bill_total))
                    ws3.cell(row=curr_row, column=9, value=float(r.var_bill_vs_erdk or Decimal("0.00")))
                    ws3.cell(row=curr_row, column=10, value="YES" if r.is_outsorted else "NO").alignment = Alignment(horizontal="center")
                    ws3.cell(row=curr_row, column=11, value=f"Run batch validation on {r.invoice_number}. Detailed error: {defect}")

                    for c in range(1, 12):
                        ws3.cell(row=curr_row, column=c).border = thin_border
                    curr_row += 1
                    bug_counter += 1

        # ----------------------------------------------------------------------
        # SHEET 4: Mathematical Validation Audit
        # ----------------------------------------------------------------------
        if math_results:
            ws4 = wb.create_sheet(title="Regulatory Math Audit")
            ws4.views.sheetView[0].showGridLines = True

            math_headers = [
                "Invoice No", "Tariff", "Component", "Formula / Breakdown",
                "Extracted (€)", "Recalculated (€)", "Variance (€)", "Tolerance Passed", "Regulatory Reference"
            ]

            for col_idx, h in enumerate(math_headers, start=1):
                cell = ws4.cell(row=1, column=col_idx, value=h)
                cell.font = font_header
                cell.fill = fill_header
                cell.alignment = Alignment(horizontal="center")

            m_row = 2
            for m in math_results:
                for comp in m.components:
                    vals = [
                        m.invoice_number,
                        m.tariff,
                        comp.component,
                        comp.formula,
                        float(comp.extracted_value),
                        float(comp.recalculated_value),
                        float(comp.variance),
                        "PASS" if comp.tolerance_passed else "FAIL",
                        comp.regulation_reference
                    ]
                    for col_idx, v in enumerate(vals, start=1):
                        cell = ws4.cell(row=m_row, column=col_idx, value=v)
                        cell.border = thin_border
                        if col_idx in (5, 6, 7) and isinstance(v, float):
                            cell.number_format = '#,##0.00 €'
                            cell.alignment = Alignment(horizontal="right")
                        if col_idx == 8:
                            cell.alignment = Alignment(horizontal="center")
                            cell.fill = fill_pass if v == "PASS" else fill_fail
                            cell.font = font_pass if v == "PASS" else font_fail
                    m_row += 1

        # Adjust Column Widths automatically
        for sheet in wb.worksheets:
            for col in sheet.columns:
                max_len = max(len(str(cell.value or '')) for cell in col)
                col_letter = get_column_letter(col[0].column)
                sheet.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 50)

        wb.save(file_path)
        return file_path

    def generate_html_report(
        self,
        summary: BatchReconciliationSummary,
        math_results: Optional[List[MathValidationResult]] = None,
        filename: Optional[str] = None
    ) -> str:
        """Generates executive HTML sign-off report with responsive layout and clean styling."""
        if not filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"SAP_Bill_Validation_Audit_{ts}.html"

        file_path = os.path.join(self.output_dir, filename)

        # Build table rows for Reconciliation Matrix
        table_rows_html = ""
        for r in summary.records:
            status_badge = f'<span class="badge {r.overall_status.value.lower()}">{r.overall_status.value}</span>'
            outsort_badge = '<span class="badge outsorted">RETENIDA</span>' if r.is_outsorted else '<span class="badge pass">NORMAL</span>'
            defects_html = f'<div class="defect-box">{"<br>".join(r.defect_details)}</div>' if r.defect_details else '<span class="text-muted">—</span>'

            table_rows_html += f"""
            <tr>
                <td><strong>{r.invoice_number}</strong></td>
                <td><code>{r.cups}</code></td>
                <td>{r.contract_account or 'N/A'}</td>
                <td><span class="badge tariff">{r.tariff}</span></td>
                <td class="amount">{format_spanish_currency(r.bill_total)}</td>
                <td class="amount">{format_spanish_currency(r.erdk_total) if r.erdk_total else 'N/A'}</td>
                <td class="amount">{format_spanish_currency(r.dfkkop_total) if r.dfkkop_total else 'N/A'}</td>
                <td class="amount {'negative' if r.var_bill_vs_erdk and abs(r.var_bill_vs_erdk) > 0.01 else ''}">{format_spanish_currency(r.var_bill_vs_erdk)}</td>
                <td style="text-align: center;">{status_badge}</td>
                <td style="text-align: center;">{outsort_badge}</td>
                <td>{defects_html}</td>
            </tr>
            """

        html_content = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SAP IS-U & CI Validation Audit Sign-off Report</title>
    <style>
        :root {{
            --primary: #1B365D;
            --primary-light: #2E5B88;
            --accent: #0070BA;
            --success: #107C41;
            --warning: #D83B01;
            --danger: #A80000;
            --bg-light: #F8FAFC;
            --border-color: #E2E8F0;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            margin: 0;
            padding: 24px;
            background-color: var(--bg-light);
            color: #1E293B;
            line-height: 1.5;
        }}
        .header-card {{
            background: linear-gradient(135deg, var(--primary) 0%, var(--primary-light) 100%);
            color: white;
            padding: 28px;
            border-radius: 12px;
            box-shadow: 0 4px 12px rgba(27, 54, 93, 0.15);
            margin-bottom: 24px;
        }}
        .header-card h1 {{ margin: 0 0 8px 0; font-size: 24px; font-weight: 700; }}
        .header-card p {{ margin: 0; opacity: 0.9; font-size: 14px; }}
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }}
        .kpi-card {{
            background: white;
            padding: 20px;
            border-radius: 10px;
            border: 1px solid var(--border-color);
            box-shadow: 0 2px 4px rgba(0,0,0,0.03);
            text-align: center;
        }}
        .kpi-card .title {{ font-size: 12px; font-weight: 600; text-transform: uppercase; color: #64748B; margin-bottom: 6px; }}
        .kpi-card .value {{ font-size: 26px; font-weight: 700; color: var(--primary); }}
        .kpi-card.pass .value {{ color: var(--success); }}
        .kpi-card.outsort .value {{ color: var(--warning); }}
        .kpi-card.fail .value {{ color: var(--danger); }}
        .section-card {{
            background: white;
            border-radius: 10px;
            border: 1px solid var(--border-color);
            box-shadow: 0 2px 4px rgba(0,0,0,0.03);
            padding: 24px;
            margin-bottom: 24px;
        }}
        .section-card h2 {{
            margin-top: 0;
            font-size: 18px;
            font-weight: 600;
            color: var(--primary);
            border-bottom: 2px solid #F1F5F9;
            padding-bottom: 12px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }}
        th {{
            background-color: #F8FAFC;
            color: #475569;
            font-weight: 600;
            text-align: left;
            padding: 10px 12px;
            border-bottom: 2px solid var(--border-color);
        }}
        td {{
            padding: 10px 12px;
            border-bottom: 1px solid var(--border-color);
            vertical-align: middle;
        }}
        tr:hover {{ background-color: #F8FAFC; }}
        .amount {{ text-align: right; font-variant-numeric: tabular-nums; }}
        .amount.negative {{ color: var(--danger); font-weight: 600; }}
        code {{ font-family: monospace; background: #EEF2F6; padding: 2px 6px; border-radius: 4px; font-size: 12px; }}
        .badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
        }}
        .badge.pass {{ background-color: #D1E7DD; color: #0F5132; }}
        .badge.fail {{ background-color: #F8D7DA; color: #842029; }}
        .badge.outsorted {{ background-color: #FFF3CD; color: #664D03; }}
        .badge.tariff {{ background-color: #E2E8F0; color: #334155; }}
        .defect-box {{
            background: #FEF2F2;
            color: #991B1B;
            padding: 6px 10px;
            border-radius: 6px;
            font-size: 11px;
            border-left: 3px solid #EF4444;
        }}
        .text-muted {{ color: #94A3B8; }}
        @media print {{
            body {{ padding: 0; background: white; }}
            .header-card {{ background: #1B365D; color: white; }}
            .section-card {{ box-shadow: none; border: 1px solid #CCC; }}
        }}
    </style>
</head>
<body>
    <div class="header-card">
        <h1>SAP IS-U & CI Automated Bill Print & Invoice Validation Suite</h1>
        <p>Spain Utility Edition — 3-Way Reconciliation & SIT/UAT Regression Audit Sign-off | Run Timestamp: {summary.timestamp}</p>
    </div>

    <div class="kpi-grid">
        <div class="kpi-card">
            <div class="title">Facturas Verificadas</div>
            <div class="value">{summary.total_invoices_tested}</div>
        </div>
        <div class="kpi-card {'pass' if summary.pass_rate_pct >= 95 else 'fail'}">
            <div class="title">Tasa de Conformidad (Pass %)</div>
            <div class="value">{summary.pass_rate_pct:.1f}%</div>
        </div>
        <div class="kpi-card {'outsort' if summary.outsorted_count > 0 else ''}">
            <div class="title">Facturas Retenidas (EASIB)</div>
            <div class="value">{summary.outsorted_count}</div>
        </div>
        <div class="kpi-card {'fail' if summary.failed_count > 0 else ''}">
            <div class="title">Defectos Críticos</div>
            <div class="value">{summary.failed_count}</div>
        </div>
        <div class="kpi-card">
            <div class="title">Impacto Financiero (€)</div>
            <div class="value">{format_spanish_currency(summary.total_financial_variance_eur)}</div>
        </div>
    </div>

    <div class="section-card">
        <h2>Matriz de Reconciliación 3-Way (Bill PDF vs ERDK vs CI Invoicing vs FI-CA)</h2>
        <table>
            <thead>
                <tr>
                    <th>Factura Nº</th>
                    <th>CUPS</th>
                    <th>Cuenta Contrato</th>
                    <th>Tarifa</th>
                    <th style="text-align: right;">Total PDF (€)</th>
                    <th style="text-align: right;">Total ERDK (€)</th>
                    <th style="text-align: right;">FI-CA Open (€)</th>
                    <th style="text-align: right;">Desviación (€)</th>
                    <th style="text-align: center;">Resultado</th>
                    <th style="text-align: center;">Retención</th>
                    <th>Detalle de Descuadre / Defecto</th>
                </tr>
            </thead>
            <tbody>
                {table_rows_html}
            </tbody>
        </table>
    </div>

    <div class="section-card">
        <h2>Certificación de Conformidad Regulatoria (CNMC / BOE)</h2>
        <p style="font-size: 13px; color: #475569;">
            Este informe audita de manera independiente la liquidación de peajes y cargos conforme a la Circular 3/2020 de la CNMC,
            el Real Decreto 148/2021, la Ley 38/1992 del Impuesto Especial sobre la Electricidad y la Ley 37/1992 del IVA.
            Los documentos con estado <strong>RETENIDA</strong> no deben ser liberados para impresión ni remesa bancaria hasta su corrección en el sistema SAP S/4HANA IS-U.
        </p>
    </div>
</body>
</html>
        """

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        return file_path
