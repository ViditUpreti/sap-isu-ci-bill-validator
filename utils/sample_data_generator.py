"""
Realistic Spanish Utility Invoice & SAP Backend Data Generator.
Generates authentic Spanish electricity (2.0TD, 3.0TD) and gas (RL.1) bills
as real PDFs (using ReportLab), XML/RDI spools, and SAP backend CSV exports
(ERDK, DFKKINV_DOC_H, DFKKINV_DOC_I, DFKKOP).
"""

import os
import io
import csv
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta
from typing import Dict, Any, List, Tuple
import qrcode
from PIL import Image

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm, mm
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from utils.locale_parser import format_spanish_currency


class SampleDataGenerator:
    """Generates realistic SIT/UAT baseline, target, XML, and SAP CSV datasets."""

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.baseline_dir = os.path.join(base_dir, "data", "baseline")
        self.target_dir = os.path.join(base_dir, "data", "target")
        self.sap_dir = os.path.join(base_dir, "data", "sap_tables")
        os.makedirs(self.baseline_dir, exist_ok=True)
        os.makedirs(self.target_dir, exist_ok=True)
        os.makedirs(self.sap_dir, exist_ok=True)

    def generate_all(self) -> Dict[str, Any]:
        """Generates all standard test scenarios."""
        scenarios = self._define_scenarios()

        erdk_rows = []
        inv_h_rows = []
        inv_i_rows = []
        dfkkop_rows = []

        generated_files = {
            "baseline_pdfs": [],
            "target_pdfs": [],
            "xml_files": [],
            "sap_tables": []
        }

        for sc in scenarios:
            inv_no = sc["invoice_number"]

            # 1. Generate Baseline PDF
            base_pdf_path = os.path.join(self.baseline_dir, f"{inv_no}_baseline.pdf")
            self._create_bill_pdf(base_pdf_path, sc, is_baseline=True)
            generated_files["baseline_pdfs"].append(base_pdf_path)

            # 2. Generate Target PDF (with intentional defects if defined)
            targ_pdf_path = os.path.join(self.target_dir, f"{inv_no}_target.pdf")
            self._create_bill_pdf(targ_pdf_path, sc, is_baseline=False)
            generated_files["target_pdfs"].append(targ_pdf_path)

            # 3. Generate XML Spool
            xml_path = os.path.join(self.target_dir, f"{inv_no}_spool.xml")
            self._create_bill_xml(xml_path, sc)
            generated_files["xml_files"].append(xml_path)

            # 4. SAP Table Records
            e_row, h_row, i_rows, op_row = self._create_sap_rows(sc)
            erdk_rows.append(e_row)
            inv_h_rows.append(h_row)
            inv_i_rows.extend(i_rows)
            dfkkop_rows.append(op_row)

        # Write SAP CSV files
        erdk_csv = os.path.join(self.sap_dir, "ERDK.csv")
        self._write_csv(erdk_csv, erdk_rows)

        inv_h_csv = os.path.join(self.sap_dir, "DFKKINV_DOC_H.csv")
        self._write_csv(inv_h_csv, inv_h_rows)

        inv_i_csv = os.path.join(self.sap_dir, "DFKKINV_DOC_I.csv")
        self._write_csv(inv_i_csv, inv_i_rows)

        dfkkop_csv = os.path.join(self.sap_dir, "DFKKOP.csv")
        self._write_csv(dfkkop_csv, dfkkop_rows)

        generated_files["sap_tables"] = [erdk_csv, inv_h_csv, inv_i_csv, dfkkop_csv]
        return generated_files

    def _write_csv(self, file_path: str, rows: List[Dict[str, Any]]) -> None:
        if not rows:
            return
        keys = list(rows[0].keys())
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys, delimiter=";")
            writer.writeheader()
            writer.writerows(rows)

    def _define_scenarios(self) -> List[Dict[str, Any]]:
        """Defines realistic test cases with normal and edge cases."""
        return [
            # Scenario 1: Clean 2.0TD Domestic (Pass)
            {
                "invoice_number": "10000001",
                "customer_name": "CARLOS MARTINEZ GARCIA",
                "fiscal_id": "12345678Z",
                "contract_account": "2000000123",
                "business_partner": "0001002345",
                "cups": "ES0021000000000001AB",
                "tariff": "2.0TD",
                "issue_date": "15/11/2026",
                "due_date": "25/11/2026",
                "period_start": "01/10/2026",
                "period_end": "31/10/2026",
                "days": 30,
                "power_periods": [
                    {"period": "P1", "kw": Decimal("4.600"), "days": 30, "price": Decimal("0.082245"), "total": Decimal("11.35")},
                    {"period": "P2", "kw": Decimal("4.600"), "days": 30, "price": Decimal("0.015320"), "total": Decimal("2.11")}
                ],
                "energy_periods": [
                    {"period": "P1", "kwh": Decimal("120.0"), "price": Decimal("0.185200"), "total": Decimal("22.22")},
                    {"period": "P2", "kwh": Decimal("110.0"), "price": Decimal("0.142100"), "total": Decimal("15.63")},
                    {"period": "P3", "kwh": Decimal("150.0"), "price": Decimal("0.098500"), "total": Decimal("14.78")}
                ],
                "iee_rate": Decimal("0.051127"),
                "bono_social": Decimal("0.19"),
                "meter_rental": Decimal("0.81"),
                "other_services": Decimal("0.00"),
                "iva_rate": Decimal("0.21"),
                "defect_type": "NONE"
            },
            # Scenario 2: Clean 2.0TD Small Business with 10% IVA (Pass)
            {
                "invoice_number": "10000002",
                "customer_name": "PANADERIA ARTESANA SL",
                "fiscal_id": "B87654321",
                "contract_account": "2000000124",
                "business_partner": "0001002346",
                "cups": "ES0021000000000002CD",
                "tariff": "2.0TD",
                "issue_date": "15/11/2026",
                "due_date": "25/11/2026",
                "period_start": "01/10/2026",
                "period_end": "31/10/2026",
                "days": 30,
                "power_periods": [
                    {"period": "P1", "kw": Decimal("9.200"), "days": 30, "price": Decimal("0.082245"), "total": Decimal("22.70")},
                    {"period": "P2", "kw": Decimal("9.200"), "days": 30, "price": Decimal("0.015320"), "total": Decimal("4.23")}
                ],
                "energy_periods": [
                    {"period": "P1", "kwh": Decimal("350.0"), "price": Decimal("0.185200"), "total": Decimal("64.82")},
                    {"period": "P2", "kwh": Decimal("280.0"), "price": Decimal("0.142100"), "total": Decimal("39.79")},
                    {"period": "P3", "kwh": Decimal("420.0"), "price": Decimal("0.098500"), "total": Decimal("41.37")}
                ],
                "iee_rate": Decimal("0.051127"),
                "bono_social": Decimal("0.19"),
                "meter_rental": Decimal("0.81"),
                "other_services": Decimal("0.00"),
                "iva_rate": Decimal("0.10"),  # Reduced rate
                "defect_type": "NONE"
            },
            # Scenario 3: 2.0TD Rounding Defect (+0.03 € in Target)
            {
                "invoice_number": "10000003",
                "customer_name": "INMOBILIARIA CENTRO SL",
                "fiscal_id": "B11223344",
                "contract_account": "2000000125",
                "business_partner": "0001002347",
                "cups": "ES0021000000000003EF",
                "tariff": "2.0TD",
                "issue_date": "15/11/2026",
                "due_date": "25/11/2026",
                "period_start": "01/10/2026",
                "period_end": "31/10/2026",
                "days": 30,
                "power_periods": [
                    {"period": "P1", "kw": Decimal("5.750"), "days": 30, "price": Decimal("0.082245"), "total": Decimal("14.19")},
                    {"period": "P2", "kw": Decimal("5.750"), "days": 30, "price": Decimal("0.015320"), "total": Decimal("2.64")}
                ],
                "energy_periods": [
                    {"period": "P1", "kwh": Decimal("180.0"), "price": Decimal("0.185200"), "total": Decimal("33.34")},
                    {"period": "P2", "kwh": Decimal("140.0"), "price": Decimal("0.142100"), "total": Decimal("19.89")},
                    {"period": "P3", "kwh": Decimal("210.0"), "price": Decimal("0.098500"), "total": Decimal("20.69")}
                ],
                "iee_rate": Decimal("0.051127"),
                "bono_social": Decimal("0.19"),
                "meter_rental": Decimal("0.81"),
                "other_services": Decimal("0.00"),
                "iva_rate": Decimal("0.21"),
                "defect_type": "ROUNDING_SHIFT"  # Target will have +0.03 €
            },
            # Scenario 4: 2.0TD Regulatory IEE Outsorting Defect (Wrong IEE 0.5% instead of 3.8%)
            {
                "invoice_number": "10000004",
                "customer_name": "MARIA DOLORES RUIZ",
                "fiscal_id": "87654321A",
                "contract_account": "2000000126",
                "business_partner": "0001002348",
                "cups": "ES0021000000000004GH",
                "tariff": "2.0TD",
                "issue_date": "15/11/2026",
                "due_date": "25/11/2026",
                "period_start": "01/10/2026",
                "period_end": "31/10/2026",
                "days": 30,
                "power_periods": [
                    {"period": "P1", "kw": Decimal("3.450"), "days": 30, "price": Decimal("0.082245"), "total": Decimal("8.51")},
                    {"period": "P2", "kw": Decimal("3.450"), "days": 30, "price": Decimal("0.015320"), "total": Decimal("1.59")}
                ],
                "energy_periods": [
                    {"period": "P1", "kwh": Decimal("85.0"), "price": Decimal("0.185200"), "total": Decimal("15.74")},
                    {"period": "P2", "kwh": Decimal("75.0"), "price": Decimal("0.142100"), "total": Decimal("10.66")},
                    {"period": "P3", "kwh": Decimal("120.0"), "price": Decimal("0.098500"), "total": Decimal("11.82")}
                ],
                "iee_rate": Decimal("0.038000"),  # Expected statutory 3.8%
                "bono_social": Decimal("0.19"),
                "meter_rental": Decimal("0.81"),
                "other_services": Decimal("0.00"),
                "iva_rate": Decimal("0.21"),
                "defect_type": "WRONG_IEE_TAX_RATE"  # Target will use 0.0050 (0.5%), causing EASIB outsort
            },
            # Scenario 5: Industrial 3.0TD (6 Periods P1..P6) Clean Pass
            {
                "invoice_number": "10000005",
                "customer_name": "TALLERES METALURGICOS SA",
                "fiscal_id": "A28000001",
                "contract_account": "2000000127",
                "business_partner": "0001002349",
                "cups": "ES0021000000000005IJ",
                "tariff": "3.0TD",
                "issue_date": "15/11/2026",
                "due_date": "25/11/2026",
                "period_start": "01/10/2026",
                "period_end": "31/10/2026",
                "days": 30,
                "power_periods": [
                    {"period": "P1", "kw": Decimal("35.000"), "days": 30, "price": Decimal("0.054320"), "total": Decimal("57.04")},
                    {"period": "P2", "kw": Decimal("35.000"), "days": 30, "price": Decimal("0.032110"), "total": Decimal("33.72")},
                    {"period": "P3", "kw": Decimal("35.000"), "days": 30, "price": Decimal("0.021050"), "total": Decimal("22.10")},
                    {"period": "P4", "kw": Decimal("35.000"), "days": 30, "price": Decimal("0.018400"), "total": Decimal("19.32")},
                    {"period": "P5", "kw": Decimal("35.000"), "days": 30, "price": Decimal("0.012500"), "total": Decimal("13.13")},
                    {"period": "P6", "kw": Decimal("35.000"), "days": 30, "price": Decimal("0.008200"), "total": Decimal("8.61")}
                ],
                "energy_periods": [
                    {"period": "P1", "kwh": Decimal("1200.0"), "price": Decimal("0.221000"), "total": Decimal("265.20")},
                    {"period": "P2", "kwh": Decimal("950.0"), "price": Decimal("0.198500"), "total": Decimal("188.58")},
                    {"period": "P3", "kwh": Decimal("800.0"), "price": Decimal("0.165400"), "total": Decimal("132.32")},
                    {"period": "P4", "kwh": Decimal("750.0"), "price": Decimal("0.142000"), "total": Decimal("106.50")},
                    {"period": "P5", "kwh": Decimal("600.0"), "price": Decimal("0.119000"), "total": Decimal("71.40")},
                    {"period": "P6", "kwh": Decimal("1500.0"), "price": Decimal("0.089500"), "total": Decimal("134.25")}
                ],
                "iee_rate": Decimal("0.051127"),
                "bono_social": Decimal("0.19"),
                "meter_rental": Decimal("1.36"),  # Triphase
                "other_services": Decimal("0.00"),
                "iva_rate": Decimal("0.21"),
                "defect_type": "NONE"
            },
            # Scenario 6: 3.0TD CI Line Item Mismatch (DFKKINV_DOC_I has different power amount)
            {
                "invoice_number": "10000006",
                "customer_name": "LOGISTICA DEL SUR SL",
                "fiscal_id": "B41000002",
                "contract_account": "2000000128",
                "business_partner": "0001002350",
                "cups": "ES0021000000000006KL",
                "tariff": "3.0TD",
                "issue_date": "15/11/2026",
                "due_date": "25/11/2026",
                "period_start": "01/10/2026",
                "period_end": "31/10/2026",
                "days": 30,
                "power_periods": [
                    {"period": "P1", "kw": Decimal("25.000"), "days": 30, "price": Decimal("0.054320"), "total": Decimal("40.74")},
                    {"period": "P2", "kw": Decimal("25.000"), "days": 30, "price": Decimal("0.032110"), "total": Decimal("24.08")}
                ],
                "energy_periods": [
                    {"period": "P1", "kwh": Decimal("800.0"), "price": Decimal("0.221000"), "total": Decimal("176.80")},
                    {"period": "P2", "kwh": Decimal("650.0"), "price": Decimal("0.198500"), "total": Decimal("129.03")}
                ],
                "iee_rate": Decimal("0.051127"),
                "bono_social": Decimal("0.19"),
                "meter_rental": Decimal("1.36"),
                "other_services": Decimal("0.00"),
                "iva_rate": Decimal("0.21"),
                "defect_type": "CI_LINE_ITEM_MISMATCH"
            },
            # Scenario 7: 2.0TD Layout Shift Defect (Table displaced vertically by 25pt)
            {
                "invoice_number": "10000007",
                "customer_name": "FRANCISCO JAVIER LOPEZ",
                "fiscal_id": "45678901B",
                "contract_account": "2000000129",
                "business_partner": "0001002351",
                "cups": "ES0021000000000007MN",
                "tariff": "2.0TD",
                "issue_date": "15/11/2026",
                "due_date": "25/11/2026",
                "period_start": "01/10/2026",
                "period_end": "31/10/2026",
                "days": 30,
                "power_periods": [
                    {"period": "P1", "kw": Decimal("4.600"), "days": 30, "price": Decimal("0.082245"), "total": Decimal("11.35")},
                    {"period": "P2", "kw": Decimal("4.600"), "days": 30, "price": Decimal("0.015320"), "total": Decimal("2.11")}
                ],
                "energy_periods": [
                    {"period": "P1", "kwh": Decimal("140.0"), "price": Decimal("0.185200"), "total": Decimal("25.93")},
                    {"period": "P2", "kwh": Decimal("120.0"), "price": Decimal("0.142100"), "total": Decimal("17.05")}
                ],
                "iee_rate": Decimal("0.051127"),
                "bono_social": Decimal("0.19"),
                "meter_rental": Decimal("0.81"),
                "other_services": Decimal("0.00"),
                "iva_rate": Decimal("0.21"),
                "defect_type": "LAYOUT_SHIFT_25PT"
            },
            # Scenario 8: FI-CA Posting Imbalance (DFKKOP open item is 82.00 € vs Bill 85.27 €)
            {
                "invoice_number": "10000008",
                "customer_name": "CONSTRUCCIONES NORTE SA",
                "fiscal_id": "A48000003",
                "contract_account": "2000000130",
                "business_partner": "0001002352",
                "cups": "ES0021000000000008OP",
                "tariff": "2.0TD",
                "issue_date": "15/11/2026",
                "due_date": "25/11/2026",
                "period_start": "01/10/2026",
                "period_end": "31/10/2026",
                "days": 30,
                "power_periods": [
                    {"period": "P1", "kw": Decimal("4.600"), "days": 30, "price": Decimal("0.082245"), "total": Decimal("11.35")},
                    {"period": "P2", "kw": Decimal("4.600"), "days": 30, "price": Decimal("0.015320"), "total": Decimal("2.11")}
                ],
                "energy_periods": [
                    {"period": "P1", "kwh": Decimal("120.0"), "price": Decimal("0.185200"), "total": Decimal("22.22")},
                    {"period": "P2", "kwh": Decimal("110.0"), "price": Decimal("0.142100"), "total": Decimal("15.63")},
                    {"period": "P3", "kwh": Decimal("150.0"), "price": Decimal("0.098500"), "total": Decimal("14.78")}
                ],
                "iee_rate": Decimal("0.051127"),
                "bono_social": Decimal("0.19"),
                "meter_rental": Decimal("0.81"),
                "other_services": Decimal("0.00"),
                "iva_rate": Decimal("0.21"),
                "defect_type": "FICA_POSTING_IMBALANCE"
            }
        ]

    def _create_bill_pdf(self, file_path: str, sc: Dict[str, Any], is_baseline: bool) -> None:
        """Generates authentic PDF bill using ReportLab."""
        defect = sc.get("defect_type", "NONE")

        # Dynamic spool changes between runs
        spool_id = f"SPOOL_0001928{sc['invoice_number'][-2:]}" if is_baseline else f"SPOOL_0002847{sc['invoice_number'][-2:]}"
        print_timestamp = "15/11/2026 10:14:22" if is_baseline else "15/11/2026 16:45:09"

        # Calculate values
        power_tot = sum((p["total"] for p in sc["power_periods"]), Decimal("0.00"))
        energy_tot = sum((e["total"] for e in sc["energy_periods"]), Decimal("0.00"))
        iee_base = power_tot + energy_tot

        iee_rate = sc["iee_rate"]
        # Apply intentional defect if target
        if not is_baseline and defect == "WRONG_IEE_TAX_RATE":
            iee_rate = Decimal("0.005000")  # 0.5% wrong rate

        iee_amt = (iee_base * iee_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        tax_base = power_tot + energy_tot + iee_amt + sc["bono_social"] + sc["meter_rental"] + sc["other_services"]

        iva_amt = (tax_base * sc["iva_rate"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_amt = tax_base + iva_amt

        if not is_baseline and defect == "ROUNDING_SHIFT":
            total_amt += Decimal("0.03")  # +0.03 rounding shift defect

        # Generate QR code for Verifactu / FacturaE
        qr_url = f"https://verifactu.agenciatributaria.gob.es/valida?nif={sc['fiscal_id']}&num={sc['invoice_number']}&fecha={sc['issue_date']}&total={total_amt}"
        qr_img = qrcode.make(qr_url)
        qr_buffer = io.BytesIO()
        qr_img.save(qr_buffer, format="PNG")
        qr_buffer.seek(0)

        # Build PDF with ReportLab
        c = canvas.Canvas(file_path, pagesize=A4)
        width, height = A4

        # Header bar (SAP Navy Blue)
        c.setFillColor(colors.HexColor("#1B365D"))
        c.rect(0, height - 35 * mm, width, 35 * mm, fill=True, stroke=False)

        # Company title
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(20 * mm, height - 18 * mm, "IBERENERGIA ESPAÑA S.A.U.")
        c.setFont("Helvetica", 9)
        c.drawString(20 * mm, height - 24 * mm, "Comercializadora de Referencia | CIF: A-12345678 | Registro Mercantil de Madrid")
        c.drawString(20 * mm, height - 30 * mm, "Servicio de Atención al Cliente: 900 100 200 | www.iberenergia.es")

        # Invoice Header Box
        c.setFillColor(colors.HexColor("#F8FAFC"))
        c.setStrokeColor(colors.HexColor("#CBD5E1"))
        c.rect(20 * mm, height - 75 * mm, width - 40 * mm, 34 * mm, fill=True, stroke=True)

        c.setFillColor(colors.HexColor("#1B365D"))
        c.setFont("Helvetica-Bold", 12)
        c.drawString(24 * mm, height - 44 * mm, "FACTURA DE ELECTRICIDAD")

        c.setFillColor(colors.HexColor("#1E293B"))
        c.setFont("Helvetica", 9)
        c.drawString(24 * mm, height - 51 * mm, f"Nº Factura: {sc['invoice_number']}")
        c.drawString(24 * mm, height - 57 * mm, f"Fecha de emisión: {sc['issue_date']}")
        c.drawString(24 * mm, height - 63 * mm, f"Fecha de cargo: {sc['due_date']}")
        c.drawString(24 * mm, height - 69 * mm, f"Período: {sc['period_start']} a {sc['period_end']} ({sc['days']} días)")

        c.drawString(95 * mm, height - 51 * mm, f"Titular: {sc['customer_name']}")
        c.drawString(95 * mm, height - 57 * mm, f"NIF/CIF: {sc['fiscal_id']}")
        c.drawString(95 * mm, height - 63 * mm, f"Cuenta contrato: {sc['contract_account']}")
        c.drawString(95 * mm, height - 69 * mm, f"CUPS: {sc['cups']}")

        c.drawString(160 * mm, height - 51 * mm, f"Tarifa: {sc['tariff']}")
        c.setFont("Helvetica-Bold", 11)
        c.drawString(160 * mm, height - 63 * mm, "TOTAL FACTURA:")
        c.setFont("Helvetica-Bold", 13)
        c.setFillColor(colors.HexColor("#0F5132"))
        c.drawString(160 * mm, height - 70 * mm, format_spanish_currency(total_amt))

        # Vertical offset if layout shift defect
        y_shift = 0
        if not is_baseline and defect == "LAYOUT_SHIFT_25PT":
            y_shift = -25  # Shift down by 25pt

        # Breakdown Section
        y_curr = height - 85 * mm + y_shift
        c.setFillColor(colors.HexColor("#1B365D"))
        c.setFont("Helvetica-Bold", 10)
        c.drawString(20 * mm, y_curr, "DETALLE DE FACTURACIÓN (Peajes, Cargos e Impuestos)")

        # Potencia Lines
        y_curr -= 6 * mm
        c.setFont("Helvetica", 8.5)
        c.setFillColor(colors.HexColor("#334155"))
        for p in sc["power_periods"]:
            c.drawString(
                24 * mm, y_curr,
                f"Potencia {p['period']}: {p['kw']:.3f} kW x {p['days']} días x {p['price']:.6f} €/kW/día = {p['total']:.2f} €"
            )
            y_curr -= 4.5 * mm

        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(24 * mm, y_curr, f"Total potencia: {power_tot:.2f} €")
        y_curr -= 7 * mm

        # Energia Lines
        c.setFont("Helvetica", 8.5)
        for e in sc["energy_periods"]:
            c.drawString(
                24 * mm, y_curr,
                f"Energía activa {e['period']}: {e['kwh']:.1f} kWh x {e['price']:.6f} €/kWh = {e['total']:.2f} €"
            )
            y_curr -= 4.5 * mm

        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(24 * mm, y_curr, f"Total energía: {energy_tot:.2f} €")
        y_curr -= 7 * mm

        # Regulatory Fees & Taxes
        c.setFont("Helvetica", 8.5)
        iee_pct_str = f"{iee_rate * Decimal('100'):.4f}%".replace('.', ',')
        c.drawString(24 * mm, y_curr, f"Impuesto sobre la electricidad: {iee_pct_str} s/ {iee_base:.2f} € = {iee_amt:.2f} €")
        y_curr -= 4.5 * mm

        c.drawString(24 * mm, y_curr, f"Financiación Bono Social: {sc['days']} días x 0,006270 €/día = {sc['bono_social']:.2f} €")
        y_curr -= 4.5 * mm

        c.drawString(24 * mm, y_curr, f"Alquiler de equipos de medida: {sc['days']} días x 0,027000 €/día = {sc['meter_rental']:.2f} €")
        y_curr -= 6 * mm

        # Base Imponible & IVA
        c.setFont("Helvetica-Bold", 9)
        c.drawString(24 * mm, y_curr, f"Base Imponible: {tax_base:.2f} €")
        y_curr -= 4.5 * mm

        iva_pct_str = f"{sc['iva_rate'] * Decimal('100'):.0f}%"
        c.drawString(24 * mm, y_curr, f"IVA ({iva_pct_str} s/ {tax_base:.2f} €) = {iva_amt:.2f} €")
        y_curr -= 7 * mm

        # Total Box
        c.setStrokeColor(colors.HexColor("#0F5132"))
        c.setFillColor(colors.HexColor("#D1E7DD"))
        c.rect(20 * mm, y_curr - 4 * mm, 120 * mm, 9 * mm, fill=True, stroke=True)
        c.setFillColor(colors.HexColor("#0F5132"))
        c.setFont("Helvetica-Bold", 11)
        c.drawString(24 * mm, y_curr, f"TOTAL IMPORTE FACTURA: {format_spanish_currency(total_amt)}")

        # Draw QR Code
        qr_rl_img = RLImage(qr_buffer, width=28 * mm, height=28 * mm)
        qr_rl_img.drawOn(c, width - 52 * mm, y_curr - 15 * mm)
        c.setFont("Helvetica", 7)
        c.setFillColor(colors.HexColor("#64748B"))
        c.drawString(width - 55 * mm, y_curr - 18 * mm, "Veri*factu / FacturaE QR")

        # Technical SAP Spool Footer (Dynamic fields for masking test)
        c.setStrokeColor(colors.HexColor("#CBD5E1"))
        c.line(20 * mm, 20 * mm, width - 20 * mm, 20 * mm)
        c.setFont("Helvetica", 7.5)
        c.drawString(20 * mm, 16 * mm, f"SAP Spool: {spool_id} | Impreso: {print_timestamp} | ADS Form: ZISU_BILL_ES_20TD")
        c.drawRightString(width - 20 * mm, 16 * mm, "Página 1 de 1 | SAP S/4HANA IS-U Release 2023")

        c.save()

    def _create_bill_xml(self, file_path: str, sc: Dict[str, Any]) -> None:
        """Generates companion SAP Print Workbench XML spool."""
        power_tot = sum((p["total"] for p in sc["power_periods"]), Decimal("0.00"))
        energy_tot = sum((e["total"] for e in sc["energy_periods"]), Decimal("0.00"))
        iee_amt = ((power_tot + energy_tot) * sc["iee_rate"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        tax_base = power_tot + energy_tot + iee_amt + sc["bono_social"] + sc["meter_rental"]
        iva_amt = (tax_base * sc["iva_rate"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_amt = tax_base + iva_amt

        xml_lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<SAP_ISU_BILL version="1.0">',
            '  <HEADER>',
            f'    <OPBEL>{sc["invoice_number"]}</OPBEL>',
            f'    <BUKRS>ES01</BUKRS>',
            f'    <PRINTDATE>{sc["issue_date"]}</PRINTDATE>',
            f'    <FAEDN>{sc["due_date"]}</FAEDN>',
            f'    <VKONT>{sc["contract_account"]}</VKONT>',
            f'    <GPART>{sc["business_partner"]}</GPART>',
            f'    <CUPS>{sc["cups"]}</CUPS>',
            f'    <TARIFF>{sc["tariff"]}</TARIFF>',
            f'    <CUSTOMER_NAME>{sc["customer_name"]}</CUSTOMER_NAME>',
            f'    <TOTAL_AMNT>{total_amt:.2f}</TOTAL_AMNT>',
            f'    <TAX_BASE>{tax_base:.2f}</TAX_BASE>',
            '  </HEADER>',
            '  <LINES>'
        ]

        for p in sc["power_periods"]:
            xml_lines.append(
                f'    <POWER_ITEM period="{p["period"]}" kw="{p["kw"]:.3f}" days="{p["days"]}" price="{p["price"]:.6f}" total="{p["total"]:.2f}"/>'
            )
        for e in sc["energy_periods"]:
            xml_lines.append(
                f'    <ENERGY_ITEM period="{e["period"]}" kwh="{e["kwh"]:.1f}" price="{e["price"]:.6f}" total="{e["total"]:.2f}"/>'
            )

        xml_lines.extend([
            f'    <TAX_ITEM type="IEE" rate="{sc["iee_rate"]:.6f}" amount="{iee_amt:.2f}"/>',
            f'    <BONO_SOCIAL>{sc["bono_social"]:.2f}</BONO_SOCIAL>',
            f'    <METER_RENTAL>{sc["meter_rental"]:.2f}</METER_RENTAL>',
            f'    <TAX_ITEM type="IVA" rate="{sc["iva_rate"]:.2f}" amount="{iva_amt:.2f}"/>',
            '  </LINES>',
            '  <SPOOL_INFO>',
            f'    <SPOOL_ID>SPOOL_0002847{sc["invoice_number"][-2:]}</SPOOL_ID>',
            '    <TIMESTAMP>15.11.2026 16:45:09</TIMESTAMP>',
            '    <FORM>ZISU_BILL_ES_20TD</FORM>',
            '  </SPOOL_INFO>',
            '</SAP_ISU_BILL>'
        ])

        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(xml_lines))

    def _create_sap_rows(self, sc: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]]:
        """Generates consistent rows for ERDK, DFKKINV_DOC_H, DFKKINV_DOC_I, DFKKOP."""
        defect = sc.get("defect_type", "NONE")
        power_tot = sum((p["total"] for p in sc["power_periods"]), Decimal("0.00"))
        energy_tot = sum((e["total"] for e in sc["energy_periods"]), Decimal("0.00"))
        iee_amt = ((power_tot + energy_tot) * sc["iee_rate"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        tax_base = power_tot + energy_tot + iee_amt + sc["bono_social"] + sc["meter_rental"]
        iva_amt = (tax_base * sc["iva_rate"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_amt = tax_base + iva_amt

        # Table ERDK
        erdk_amt = total_amt
        erdk_row = {
            "MANDT": "100",
            "OPBEL": sc["invoice_number"],
            "INVOICED_BILL": f"00{sc['invoice_number']}",
            "BUKRS": "ES01",
            "TOTAL_AMNT": f"{erdk_amt:.2f}",
            "WAERS": "EUR",
            "VKONT": sc["contract_account"],
            "GPART": sc["business_partner"],
            "PRINTDATE": sc["issue_date"],
            "FORM": "ZISU_BILL_ES_20TD",
            "DRUCKSTATUS": "3"
        }

        # Table DFKKINV_DOC_H
        inv_h_row = {
            "MANDT": "100",
            "INVDOCNO": sc["invoice_number"],
            "APPLK": "U",
            "GPART": sc["business_partner"],
            "VKONT": sc["contract_account"],
            "TOTAL_AMNT": f"{tax_base:.2f}",
            "INV_CURR": "EUR",
            "INV_STATUS": "0",
            "CRDATE": sc["issue_date"]
        }

        # Table DFKKINV_DOC_I
        inv_i_rows = []
        item_counter = 1

        # Power line
        sap_power_amt = power_tot
        if defect == "CI_LINE_ITEM_MISMATCH":
            sap_power_amt += Decimal("15.00")  # intentional defect in SAP CI item

        inv_i_rows.append({
            "MANDT": "100",
            "INVDOCNO": sc["invoice_number"],
            "INVDOCITEM": f"{item_counter:05d}",
            "ITEMTYPE": "0EXT",
            "SUB_AP_ID": "POWER",
            "TAX_CODE": "V1",
            "BETRW": f"{sap_power_amt:.2f}",
            "NETTO_AMNT": f"{sap_power_amt:.2f}"
        })
        item_counter += 1

        # Energy line
        inv_i_rows.append({
            "MANDT": "100",
            "INVDOCNO": sc["invoice_number"],
            "INVDOCITEM": f"{item_counter:05d}",
            "ITEMTYPE": "0EXT",
            "SUB_AP_ID": "ENERGY",
            "TAX_CODE": "V1",
            "BETRW": f"{energy_tot:.2f}",
            "NETTO_AMNT": f"{energy_tot:.2f}"
        })
        item_counter += 1

        # IEE line
        inv_i_rows.append({
            "MANDT": "100",
            "INVDOCNO": sc["invoice_number"],
            "INVDOCITEM": f"{item_counter:05d}",
            "ITEMTYPE": "0EXT",
            "SUB_AP_ID": "IEE",
            "TAX_CODE": "I1",
            "BETRW": f"{iee_amt:.2f}",
            "NETTO_AMNT": f"{iee_amt:.2f}"
        })
        item_counter += 1

        # Meter line
        inv_i_rows.append({
            "MANDT": "100",
            "INVDOCNO": sc["invoice_number"],
            "INVDOCITEM": f"{item_counter:05d}",
            "ITEMTYPE": "0EXT",
            "SUB_AP_ID": "EQUIPMENT",
            "TAX_CODE": "V1",
            "BETRW": f"{sc['meter_rental']:.2f}",
            "NETTO_AMNT": f"{sc['meter_rental']:.2f}"
        })
        item_counter += 1

        # Bono Social line
        inv_i_rows.append({
            "MANDT": "100",
            "INVDOCNO": sc["invoice_number"],
            "INVDOCITEM": f"{item_counter:05d}",
            "ITEMTYPE": "0EXT",
            "SUB_AP_ID": "BONO_SOC",
            "TAX_CODE": "V1",
            "BETRW": f"{sc['bono_social']:.2f}",
            "NETTO_AMNT": f"{sc['bono_social']:.2f}"
        })
        item_counter += 1

        # VAT line
        inv_i_rows.append({
            "MANDT": "100",
            "INVDOCNO": sc["invoice_number"],
            "INVDOCITEM": f"{item_counter:05d}",
            "ITEMTYPE": "0EXT",
            "SUB_AP_ID": "VAT",
            "TAX_CODE": "V1",
            "BETRW": f"{iva_amt:.2f}",
            "NETTO_AMNT": f"{iva_amt:.2f}"
        })

        # Table DFKKOP
        dfkkop_amt = total_amt
        if defect == "FICA_POSTING_IMBALANCE":
            dfkkop_amt = total_amt - Decimal("3.27")  # 82.00 € instead of 85.27 €

        dfkkop_row = {
            "MANDT": "100",
            "OPBEL": sc["invoice_number"],
            "OPUPK": "0001",
            "BUKRS": "ES01",
            "VKONT": sc["contract_account"],
            "GPART": sc["business_partner"],
            "FAEDN": sc["due_date"],
            "BETRW": f"{dfkkop_amt:.2f}",
            "STAKU": "",
            "AUGST": "",
            "HVORG": "0100",
            "TVORG": "0001",
            "BLART": "ED"
        }

        return erdk_row, inv_h_row, inv_i_rows, dfkkop_row
