"""
PDF Extractor for Spanish Utility Invoices (SAP IS-U & CI).
Extracts structured billing data, line items, regulatory components,
bounding boxes, spool metadata, and Verifactu/FacturaE QR codes using
pdfplumber and PyMuPDF.
"""

import re
import os
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple
from datetime import date

import fitz  # PyMuPDF
import pdfplumber

from src.models import (
    SpanishBillExtract, TariffType, PowerPeriod, EnergyPeriod,
    InvoiceItem
)
from utils.locale_parser import (
    parse_spanish_decimal, parse_spanish_date
)


class SpanishBillPDFExtractor:
    """
    Robust coordinate- and regex-based extractor for Spanish energy bills
    (Endesa, Iberdrola, Naturgy, EDP, and custom SAP ADS/SmartForm formats).
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.regexes = self.config.get('regex_patterns', {})
        self.dynamic_masking = self.config.get('dynamic_masking', {}).get('ignore_patterns', [])

    def extract(self, pdf_path: str) -> SpanishBillExtract:
        """Extracts complete structured billing data and coordinate map from PDF."""
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        full_text = ""
        page_boxes: Dict[str, Any] = {}
        doc = fitz.open(pdf_path)

        try:
            # Extract text and words with coordinates using fitz
            for page_idx, page in enumerate(doc):
                page_text = page.get_text("text")
                full_text += f"\n--- PAGE {page_idx+1} ---\n" + page_text

                # Extract bounding boxes for words
                words = page.get_text("words")  # [x0, y0, x1, y1, word, block_no, line_no, word_no]
                page_boxes[f"page_{page_idx+1}"] = [
                    {"bbox": [w[0], w[1], w[2], w[3]], "text": w[4]} for w in words
                ]
        finally:
            doc.close()

        # Also extract structured tables using pdfplumber for high-fidelity tabulations
        tables = []
        try:
            with pdfplumber.open(pdf_path) as plumber_pdf:
                for p in plumber_pdf.pages:
                    extracted_tables = p.extract_tables()
                    if extracted_tables:
                        tables.extend(extracted_tables)
        except Exception:
            pass

        # Parse Fields
        invoice_number = self._extract_invoice_number(full_text)
        cups = self._extract_cups(full_text)
        tariff = self._extract_tariff(full_text)
        issue_date = self._extract_issue_date(full_text)
        due_date = self._extract_due_date(full_text)
        vkont = self._extract_vkont(full_text)
        gpart = self._extract_gpart(full_text)
        fiscal_id = self._extract_fiscal_id(full_text)
        customer_name, supply_address = self._extract_customer_info(full_text)
        period_start, period_end, days = self._extract_billing_period(full_text)

        # Power & Energy Breakdowns
        power_periods, power_total = self._extract_power_periods(full_text, days)
        energy_periods, energy_total = self._extract_energy_periods(full_text)

        # Regulatory & Taxes
        iee_base, iee_rate, iee_amount = self._extract_iee(full_text, power_total, energy_total)
        bono_social = self._extract_bono_social(full_text, days)
        meter_rental = self._extract_meter_rental(full_text)
        other_services = self._extract_other_services(full_text)
        tax_base = self._extract_tax_base(full_text)
        iva_rate, iva_amount = self._extract_iva(full_text, tax_base)
        total_amount = self._extract_total_amount(full_text)

        # Spool & Metadata
        spool_id = self._extract_spool_id(full_text)
        print_timestamp = self._extract_print_timestamp(full_text)
        form_name = self._extract_form_name(full_text)
        qr_content = self._extract_qr_code(pdf_path)

        # Calculate bounding box coordinates for key fields
        key_coords = self._locate_field_coordinates(pdf_path, {
            "invoice_number": invoice_number,
            "cups": cups,
            "total_amount": total_amount,
            "tax_base": tax_base,
            "power_total": power_total,
            "energy_total": energy_total,
            "spool_id": spool_id
        })

        return SpanishBillExtract(
            invoice_number=invoice_number or "UNKNOWN_INV",
            fiscal_id=fiscal_id,
            business_partner=gpart,
            contract_account=vkont,
            cups=cups or "ES0000000000000000XX",
            tariff=tariff,
            issue_date=issue_date,
            due_date=due_date,
            billing_period_start=period_start,
            billing_period_end=period_end,
            billing_days=days,
            customer_name=customer_name,
            supply_address=supply_address,
            power_periods=power_periods,
            energy_periods=energy_periods,
            power_total=power_total,
            energy_total=energy_total,
            iee_base=iee_base,
            iee_rate=iee_rate,
            iee_amount=iee_amount,
            bono_social_amount=bono_social,
            meter_rental_amount=meter_rental,
            other_services_amount=other_services,
            tax_base=tax_base,
            iva_rate=iva_rate,
            iva_amount=iva_amount,
            total_amount=total_amount,
            spool_id=spool_id,
            print_timestamp=print_timestamp,
            form_name=form_name,
            qr_content=qr_content,
            source_file=pdf_path,
            bounding_boxes=key_coords
        )

    # --------------------------------------------------------------------------
    # Field Parsing Helpers
    # --------------------------------------------------------------------------

    def _extract_invoice_number(self, text: str) -> str:
        # Match "Factura Nº: ES01-2026-0001001" or "Nº Factura: 10000001" or "Factura: ..."
        m = re.search(r'(?:N[ºo]\.?\s*Factura|Factura\s*N[ºo]\.?|Factura\s*n[uú]mero|N[úu]mero\s*de\s*factura)[:\s]*([A-Z0-9\-_/]+)', text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        # Fallback to SAP OPBEL pattern (10 digits)
        m2 = re.search(r'\b(1[0-9]{9}|[A-Z]{2,4}\d{8,10})\b', text)
        return m2.group(1).strip() if m2 else "UNKNOWN_INV"

    def _extract_cups(self, text: str) -> str:
        m = re.search(r'\b(ES\d{16}[A-Z]{2}[0-9A-Z]{0,2})\b', text)
        return m.group(1).strip() if m else "ES0000000000000000XX"

    def _extract_tariff(self, text: str) -> TariffType:
        if re.search(r'\b2\.0\s*TD\b', text, re.IGNORECASE):
            return TariffType.TARIFF_20TD
        elif re.search(r'\b3\.0\s*TD\b', text, re.IGNORECASE):
            return TariffType.TARIFF_30TD
        elif re.search(r'\b6\.1\s*TD\b', text, re.IGNORECASE):
            return TariffType.TARIFF_61TD
        elif re.search(r'\bRL\.?1\b', text, re.IGNORECASE):
            return TariffType.TARIFF_RL1
        elif re.search(r'\bRL\.?2\b', text, re.IGNORECASE):
            return TariffType.TARIFF_RL2
        elif re.search(r'\bRL\.?3\b', text, re.IGNORECASE):
            return TariffType.TARIFF_RL3
        return TariffType.TARIFF_20TD

    def _extract_issue_date(self, text: str) -> Optional[date]:
        m = re.search(r'(?:Fecha\s*de\s*emisi[óo]n|Fecha\s*emisi[óo]n|Fecha\s*factura)[:\s]*(\d{2}[\/\.-]\d{2}[\/\.-]\d{4})', text, re.IGNORECASE)
        if m:
            return parse_spanish_date(m.group(1))
        return parse_spanish_date(text)

    def _extract_due_date(self, text: str) -> Optional[date]:
        m = re.search(r'(?:Fecha\s*de\s*cargo|Fecha\s*cargo|Fecha\s*vencimiento|L[íi]mite\s*de\s*pago)[:\s]*(\d{2}[\/\.-]\d{2}[\/\.-]\d{4})', text, re.IGNORECASE)
        if m:
            return parse_spanish_date(m.group(1))
        return None

    def _extract_vkont(self, text: str) -> Optional[str]:
        # Contract Account (Cuenta Contrato SAP VKONT: 10 to 12 digits)
        m = re.search(r'(?:Cuenta\s*contrato|Referencia\s*de\s*pago|N[ºo]\s*Contrato|Cta\.\s*Contrato)[:\s]*([0-9]{8,12})', text, re.IGNORECASE)
        return m.group(1).strip() if m else None

    def _extract_gpart(self, text: str) -> Optional[str]:
        # Business Partner (Interlocutor Comercial GPART: 10 digits)
        m = re.search(r'(?:Interlocutor\s*comercial|N[ºo]\s*Cliente|C[óo]digo\s*de\s*cliente|Titular\s*ID)[:\s]*([0-9]{8,10})', text, re.IGNORECASE)
        return m.group(1).strip() if m else None

    def _extract_fiscal_id(self, text: str) -> Optional[str]:
        m = re.search(r'(?:NIF|CIF|NIE|DNI)[:\s]*([ABCDEFGHJNPQRSUVWXYZ]?\d{7,8}[A-Z0-9])', text, re.IGNORECASE)
        return m.group(1).strip() if m else None

    def _extract_customer_info(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        name, addr = None, None
        m_name = re.search(r'(?:Titular|Cliente|Nombre)[:\s]*([A-ZÁÉÍÓÚÑa-záéíóúñ\s,.]+?)(?=\n|NIF|CIF|Direcci[óo]n)', text, re.IGNORECASE)
        if m_name:
            name = m_name.group(1).strip()
        m_addr = re.search(r'(?:Direcci[óo]n\s*del\s*suministro|Punto\s*de\s*suministro)[:\s]*([A-ZÁÉÍÓÚÑa-záéíóúñ0-9\s,ºª.-]+?)(?=\n|CUPS|Tarifa)', text, re.IGNORECASE)
        if m_addr:
            addr = m_addr.group(1).strip()
        return name, addr

    def _extract_billing_period(self, text: str) -> Tuple[Optional[date], Optional[date], int]:
        m = re.search(r'(?:Per[íi]odo\s*de\s*facturaci[óo]n|Per[íi]odo)[:\s]*(\d{2}[\/\.-]\d{2}[\/\.-]\d{4})\s*(?:a|al|-)\s*(\d{2}[\/\.-]\d{2}[\/\.-]\d{4})(?:\s*\(?(\d+)\s*d[íi]as\)?)?', text, re.IGNORECASE)
        if m:
            start_date = parse_spanish_date(m.group(1))
            end_date = parse_spanish_date(m.group(2))
            days = int(m.group(3)) if m.group(3) else 30
            if start_date and end_date and not m.group(3):
                days = (end_date - start_date).days
            return start_date, end_date, max(1, days)
        return None, None, 30

    def _extract_power_periods(self, text: str, default_days: int) -> Tuple[List[PowerPeriod], Decimal]:
        periods: List[PowerPeriod] = []
        total = Decimal("0.00")

        # Regex to match: "Potencia punta (P1): 4,600 kW x 30 días x 0,082245 €/kW/día = 11,35 €"
        # or similar lines
        p_lines = re.findall(
            r'(?:Potencia\s*(?:punta|llano|valle)?\s*\(?(P[1-6])\)?|T[ée]rmino\s*potencia\s*(P[1-6]))[:\s]*'
            r'([0-9.,]+)\s*kW\s*(?:[x*]\s*([0-9]+)\s*d[íi]as)?\s*[x*]\s*([0-9.,]+)\s*(?:€\/kW(?:\/d[íi]a)?)\s*=\s*([0-9.,]+)\s*€?',
            text, re.IGNORECASE
        )

        for line in p_lines:
            period_name = (line[0] or line[1]).upper()
            kw = parse_spanish_decimal(line[2]) or Decimal("0.00")
            days = int(line[3]) if line[3] else default_days
            price = parse_spanish_decimal(line[4]) or Decimal("0.000000")
            period_total = parse_spanish_decimal(line[5]) or Decimal("0.00")

            periods.append(PowerPeriod(
                period=period_name,
                contracted_power_kw=kw,
                days=days,
                unit_price_eur_kw_day=price,
                total_eur=period_total
            ))
            total += period_total

        # Check for explicit subtotal: "Total potencia: 13,46 €"
        m_tot = re.search(r'(?:Total\s*t[ée]rmino\s*potencia|Total\s*potencia|Importe\s*potencia)[:\s]*([0-9.,]+)\s*€?', text, re.IGNORECASE)
        if m_tot:
            extracted_tot = parse_spanish_decimal(m_tot.group(1))
            if extracted_tot is not None:
                total = extracted_tot

        return periods, total

    def _extract_energy_periods(self, text: str) -> Tuple[List[EnergyPeriod], Decimal]:
        periods: List[EnergyPeriod] = []
        total = Decimal("0.00")

        # Regex to match: "Energía punta (P1): 120 kWh x 0,185200 €/kWh = 22,22 €"
        e_lines = re.findall(
            r'(?:Energ[íi]a\s*(?:activa)?\s*(?:punta|llano|valle)?\s*\(?(P[1-6])\)?|T[ée]rmino\s*energ[íi]a\s*(P[1-6]))[:\s]*'
            r'([0-9.,]+)\s*kWh\s*[x*]\s*([0-9.,]+)\s*(?:€\/kWh)\s*=\s*([0-9.,]+)\s*€?',
            text, re.IGNORECASE
        )

        for line in e_lines:
            period_name = (line[0] or line[1]).upper()
            kwh = parse_spanish_decimal(line[2]) or Decimal("0.00")
            price = parse_spanish_decimal(line[3]) or Decimal("0.000000")
            period_total = parse_spanish_decimal(line[4]) or Decimal("0.00")

            periods.append(EnergyPeriod(
                period=period_name,
                consumption_kwh=kwh,
                unit_price_eur_kwh=price,
                total_eur=period_total
            ))
            total += period_total

        # Check for explicit subtotal: "Total energía: 52,63 €"
        m_tot = re.search(r'(?:Total\s*t[ée]rmino\s*energ[íi]a|Total\s*energ[íi]a|Importe\s*energ[íi]a)[:\s]*([0-9.,]+)\s*€?', text, re.IGNORECASE)
        if m_tot:
            extracted_tot = parse_spanish_decimal(m_tot.group(1))
            if extracted_tot is not None:
                total = extracted_tot

        return periods, total

    def _extract_iee(self, text: str, power_total: Decimal, energy_total: Decimal) -> Tuple[Decimal, Decimal, Decimal]:
        # Impuesto sobre la electricidad: 5,11269632% s/ 66,09 € = 3,38 €
        m = re.search(
            r'(?:Impuesto\s*(?:sobre\s*la)?\s*electricidad|IEE|Impuesto\s*el[ée]ctrico)[:\s]*'
            r'([0-9.,]+)\s*%\s*(?:s\/\s*([0-9.,]+)\s*€?)?\s*=\s*([0-9.,]+)\s*€?',
            text, re.IGNORECASE
        )
        if m:
            rate_pct = parse_spanish_decimal(m.group(1)) or Decimal("5.1127")
            rate = rate_pct / Decimal("100")
            base = parse_spanish_decimal(m.group(2)) if m.group(2) else (power_total + energy_total)
            amt = parse_spanish_decimal(m.group(3)) or Decimal("0.00")
            return base, rate, amt

        # Default fallback
        base = power_total + energy_total
        return base, Decimal("0.051127"), Decimal("0.00")

    def _extract_bono_social(self, text: str, days: int) -> Decimal:
        m = re.search(r'(?:Financiaci[óo]n\s*Bono\s*Social|Bono\s*Social)[:\s]*(?:[0-9.,]+\s*d[íi]as\s*[x*]\s*[0-9.,]+\s*€\/d[íi]a\s*=\s*)?([0-9.,]+)\s*€?', text, re.IGNORECASE)
        if m:
            return parse_spanish_decimal(m.group(1)) or Decimal("0.00")
        return Decimal("0.00")

    def _extract_meter_rental(self, text: str) -> Decimal:
        m = re.search(r'(?:Alquiler\s*de\s*(?:equipos\s*de\s*medida|contador)|Equipos\s*de\s*medida)[:\s]*(?:[0-9.,]+\s*d[íi]as\s*[x*]\s*[0-9.,]+\s*€\/d[íi]a\s*=\s*)?([0-9.,]+)\s*€?', text, re.IGNORECASE)
        if m:
            return parse_spanish_decimal(m.group(1)) or Decimal("0.00")
        return Decimal("0.00")

    def _extract_other_services(self, text: str) -> Decimal:
        m = re.search(r'(?:Servicios\s*adicionales|Mantenimiento|Otros\s*conceptos)[:\s]*([0-9.,]+)\s*€?', text, re.IGNORECASE)
        if m:
            return parse_spanish_decimal(m.group(1)) or Decimal("0.00")
        return Decimal("0.00")

    def _extract_tax_base(self, text: str) -> Decimal:
        m = re.search(r'(?:Base\s*imponible|Subtotal\s*sujeto\s*a\s*IVA)[:\s]*([0-9.,]+)\s*€?', text, re.IGNORECASE)
        if m:
            return parse_spanish_decimal(m.group(1)) or Decimal("0.00")
        return Decimal("0.00")

    def _extract_iva(self, text: str, tax_base: Decimal) -> Tuple[Decimal, Decimal]:
        # IVA (21% s/ 70,47 €) = 14,80 € or IVA 21%: 14,80 €
        m = re.search(
            r'(?:IVA|I\.V\.A\.)\s*\(?([0-9.,]+)\s*%\s*(?:s\/\s*[0-9.,]+\s*€?)?\)?[:\s=]*([0-9.,]+)\s*€?',
            text, re.IGNORECASE
        )
        if m:
            rate_pct = parse_spanish_decimal(m.group(1)) or Decimal("21.00")
            rate = rate_pct / Decimal("100")
            amt = parse_spanish_decimal(m.group(2)) or Decimal("0.00")
            return rate, amt
        return Decimal("0.21"), Decimal("0.00")

    def _extract_total_amount(self, text: str) -> Decimal:
        # Match "TOTAL IMPORTE FACTURA: 85,27 €" or "TOTAL FACTURA: 85,27 €"
        m = re.search(r'(?:TOTAL\s*IMPORTE\s*FACTURA|TOTAL\s*FACTURA|TOTAL\s*A\s*PAGAR|IMPORTE\s*TOTAL)[:\s]*([0-9.,]+)\s*€?', text, re.IGNORECASE)
        if m:
            return parse_spanish_decimal(m.group(1)) or Decimal("0.00")
        return Decimal("0.00")

    def _extract_spool_id(self, text: str) -> Optional[str]:
        m = re.search(r'(?:SAP\s*Spool|SPOOL|ORDEN\s*SPOOL)[:\s_-]*([A-Z0-9_-]+)', text, re.IGNORECASE)
        return m.group(1).strip() if m else None

    def _extract_print_timestamp(self, text: str) -> Optional[str]:
        m = re.search(r'(?:Impreso|Fecha\s*impresi[óo]n|Generado)[:\s]*(\d{2}[\/\.-]\d{2}[\/\.-]\d{4}\s+\d{2}:\d{2}(?::\d{2})?)', text, re.IGNORECASE)
        return m.group(1).strip() if m else None

    def _extract_form_name(self, text: str) -> Optional[str]:
        m = re.search(r'(?:ADS\s*Form|Formulario|EFRM)[:\s]*([A-Z0-9_]+)', text, re.IGNORECASE)
        return m.group(1).strip() if m else None

    def _extract_qr_code(self, pdf_path: str) -> Optional[str]:
        """Extracts QR code from PDF pages using PyMuPDF and decodes text if present."""
        try:
            doc = fitz.open(pdf_path)
            for page in doc:
                # Look for QR payload embedded in link annotations or metadata
                for link in page.get_links():
                    uri = link.get("uri", "")
                    if "verifactu" in uri.lower() or "facturae" in uri.lower():
                        doc.close()
                        return uri

                # Look for QR text in text layer (often printed as accessible alternative)
                text = page.get_text("text")
                m = re.search(r'(https?:\/\/(?:verifactu|agenciatributaria|facturae)[^\s\n]+)', text, re.IGNORECASE)
                if m:
                    doc.close()
                    return m.group(1)
            doc.close()
        except Exception:
            pass
        return None

    def _locate_field_coordinates(self, pdf_path: str, fields: Dict[str, Any]) -> Dict[str, List[float]]:
        """Locates exact bounding boxes [x0, y0, x1, y1] for fields across pages."""
        coords: Dict[str, List[float]] = {}
        try:
            doc = fitz.open(pdf_path)
            for page in doc:
                for field_key, val in fields.items():
                    if val is None:
                        continue
                    # Search text representation
                    search_str = str(val)
                    if isinstance(val, Decimal):
                        search_str = f"{val:.2f}".replace('.', ',')
                    rects = page.search_for(search_str)
                    if rects:
                        r = rects[0]
                        coords[field_key] = [r.x0, r.y0, r.x1, r.y1]
            doc.close()
        except Exception:
            pass
        return coords
