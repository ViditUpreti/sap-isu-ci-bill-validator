"""
XML & RDI Parser for SAP Print Workbench (EFRM) Spool Streams.
Parses SAP SmartForms / ADS XML / XSF spools and legacy RDI (Raw Data Interface)
output into normalized SpanishBillExtract models.
"""

import os
import re
from decimal import Decimal
from typing import Optional, Dict, Any, List
from datetime import date

try:
    from lxml import etree
except ImportError:
    import xml.etree.ElementTree as etree

from src.models import (
    SpanishBillExtract, TariffType, PowerPeriod, EnergyPeriod, InvoiceItem
)
from utils.locale_parser import (
    parse_spanish_decimal, parse_spanish_date
)


class SAPXmlRdiParser:
    """
    Parser for SAP Print Workbench outputs:
    - ADS XML / XSF spools
    - SmartForms XML spools
    - Legacy RDI flat text spool records
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    def parse_file(self, file_path: str) -> SpanishBillExtract:
        """Parses file auto-detecting XML/XSF or RDI format."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Spool file not found: {file_path}")

        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read().strip()

        if content.startswith("<?xml") or "<" in content[:200]:
            return self.parse_xml(content, source_file=file_path)
        else:
            return self.parse_rdi(content, source_file=file_path)

    def parse_xml(self, xml_content: str, source_file: Optional[str] = None) -> SpanishBillExtract:
        """Parses structured SAP IS-U XML / XSF payload."""
        root = etree.fromstring(xml_content.encode('utf-8'))

        def _find_val(tag_names: List[str], default: str = "") -> str:
            for tag in tag_names:
                # search anywhere in tree
                elem = root.find(f".//{tag}")
                if elem is not None and elem.text:
                    return elem.text.strip()
            return default

        invoice_number = _find_val(["OPBEL", "INVOICE_NUMBER", "DOC_NUM", "FacturaNo"], "UNKNOWN_INV")
        cups = _find_val(["CUPS", "POINT_OF_DELIVERY"], "ES0000000000000000XX")
        vkont = _find_val(["VKONT", "CONTRACT_ACCOUNT", "CuentaContrato"], None)
        gpart = _find_val(["GPART", "BUSINESS_PARTNER", "Cliente"], None)
        fiscal_id = _find_val(["NIF", "CIF", "FISCAL_ID", "TAX_NUM"], None)
        customer_name = _find_val(["NAME1", "CUSTOMER_NAME", "Titular"], None)
        supply_address = _find_val(["ADDRESS", "SUPPLY_ADDR", "Direccion"], None)
        tariff_raw = _find_val(["TARIFF", "TARIFART", "Tarifa"], "2.0TD")

        tariff = TariffType.TARIFF_20TD
        for t in TariffType:
            if t.value in tariff_raw:
                tariff = t
                break

        issue_date = parse_spanish_date(_find_val(["PRINTDATE", "ISSUE_DATE", "BUDAT", "FechaEmision"]))
        due_date = parse_spanish_date(_find_val(["FAEDN", "DUE_DATE", "FechaCargo"]))
        period_start = parse_spanish_date(_find_val(["AB", "DATE_FROM", "PeriodoDesde"]))
        period_end = parse_spanish_date(_find_val(["BIS", "DATE_TO", "PeriodoHasta"]))
        days_str = _find_val(["DAYS", "TAGS", "Dias"], "30")
        billing_days = int(days_str) if days_str.isdigit() else 30

        # Power Periods
        power_periods: List[PowerPeriod] = []
        power_elems = root.findall(".//POWER_ITEM") or root.findall(".//POTENCIA") or root.findall(".//LINE[@type='POWER']")
        power_total = Decimal("0.00")
        for p in power_elems:
            period_name = p.get("period") or p.findtext("PERIOD") or "P1"
            kw = parse_spanish_decimal(p.get("kw") or p.findtext("KW") or "0.00")
            d = int(p.get("days") or p.findtext("DAYS") or str(billing_days))
            price = parse_spanish_decimal(p.get("price") or p.findtext("PRICE") or "0.00")
            tot = parse_spanish_decimal(p.get("total") or p.findtext("TOTAL") or "0.00")
            power_periods.append(PowerPeriod(
                period=period_name,
                contracted_power_kw=kw or Decimal("0.00"),
                days=d,
                unit_price_eur_kw_day=price or Decimal("0.00"),
                total_eur=tot or Decimal("0.00")
            ))
            power_total += (tot or Decimal("0.00"))

        if not power_total:
            power_total = parse_spanish_decimal(_find_val(["TOTAL_POWER", "POTENCIA_TOTAL", "TOTAL_POTENCIA"], "0.00")) or Decimal("0.00")

        # Energy Periods
        energy_periods: List[EnergyPeriod] = []
        energy_elems = root.findall(".//ENERGY_ITEM") or root.findall(".//ENERGIA") or root.findall(".//LINE[@type='ENERGY']")
        energy_total = Decimal("0.00")
        for e in energy_elems:
            period_name = e.get("period") or e.findtext("PERIOD") or "P1"
            kwh = parse_spanish_decimal(e.get("kwh") or e.findtext("KWH") or "0.00")
            price = parse_spanish_decimal(e.get("price") or e.findtext("PRICE") or "0.00")
            tot = parse_spanish_decimal(e.get("total") or e.findtext("TOTAL") or "0.00")
            energy_periods.append(EnergyPeriod(
                period=period_name,
                consumption_kwh=kwh or Decimal("0.00"),
                unit_price_eur_kwh=price or Decimal("0.00"),
                total_eur=tot or Decimal("0.00")
            ))
            energy_total += (tot or Decimal("0.00"))

        if not energy_total:
            energy_total = parse_spanish_decimal(_find_val(["TOTAL_ENERGY", "ENERGIA_TOTAL", "TOTAL_ENERGIA"], "0.00")) or Decimal("0.00")

        # Taxes & Regulatory
        # Check XML attributes if <TAX_ITEM type="IEE" amount="..."/>
        iee_elem = root.find(".//TAX_ITEM[@type='IEE']")
        if iee_elem is not None:
            iee_amount = parse_spanish_decimal(iee_elem.get("amount") or iee_elem.text or "0.00") or Decimal("0.00")
            iee_rate = parse_spanish_decimal(iee_elem.get("rate") or "0.051127") or Decimal("0.051127")
            iee_base = power_total + energy_total
        else:
            iee_base = parse_spanish_decimal(_find_val(["IEE_BASE", "BASE_IEE"], str(power_total + energy_total))) or (power_total + energy_total)
            iee_rate = parse_spanish_decimal(_find_val(["IEE_RATE", "TASA_IEE"], "0.051127")) or Decimal("0.051127")
            iee_amount = parse_spanish_decimal(_find_val(["IEE_AMOUNT", "IMPORTE_IEE"], "0.00")) or Decimal("0.00")

        if iee_rate > Decimal("1.0"):  # percentage like 5.1127%
            iee_rate = iee_rate / Decimal("100")

        bono_social = parse_spanish_decimal(_find_val(["BONO_SOCIAL", "BONO_SOC_AMNT"], "0.00")) or Decimal("0.00")
        meter_rental = parse_spanish_decimal(_find_val(["METER_RENTAL", "ALQUILER_CONTADOR", "EQUIPMENT"], "0.00")) or Decimal("0.00")
        other_services = parse_spanish_decimal(_find_val(["OTHER_SERVICES", "MANTENIMIENTO"], "0.00")) or Decimal("0.00")

        tax_base = parse_spanish_decimal(_find_val(["TAX_BASE", "BASE_IMPONIBLE", "NETTO_AMNT"], "0.00")) or Decimal("0.00")

        iva_elem = root.find(".//TAX_ITEM[@type='IVA']")
        if iva_elem is not None:
            iva_amount = parse_spanish_decimal(iva_elem.get("amount") or iva_elem.text or "0.00") or Decimal("0.00")
            iva_rate = parse_spanish_decimal(iva_elem.get("rate") or "0.21") or Decimal("0.21")
        else:
            iva_rate = parse_spanish_decimal(_find_val(["IVA_RATE", "TASA_IVA"], "0.21")) or Decimal("0.21")
            iva_amount = parse_spanish_decimal(_find_val(["IVA_AMOUNT", "IMPORTE_IVA"], "0.00")) or Decimal("0.00")

        if iva_rate > Decimal("1.0"):
            iva_rate = iva_rate / Decimal("100")

        total_amount = parse_spanish_decimal(_find_val(["TOTAL_AMNT", "TOTAL_FACTURA", "GROSS_AMNT"], "0.00")) or Decimal("0.00")

        # Spool metadata
        spool_id = _find_val(["SPOOL_ID", "SPOOLNO", "SPOOL"])
        print_timestamp = _find_val(["TIMESTAMP", "PRINT_TIME", "CRDATE"])
        form_name = _find_val(["FORM", "FORM_NAME", "ADS_FORM"])
        qr_content = _find_val(["QR_DATA", "VERIFACTU_URL", "QR_CODE"])

        return SpanishBillExtract(
            invoice_number=invoice_number,
            fiscal_id=fiscal_id,
            business_partner=gpart,
            contract_account=vkont,
            cups=cups,
            tariff=tariff,
            issue_date=issue_date,
            due_date=due_date,
            billing_period_start=period_start,
            billing_period_end=period_end,
            billing_days=billing_days,
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
            source_file=source_file
        )

    def parse_rdi(self, rdi_content: str, source_file: Optional[str] = None) -> SpanishBillExtract:
        """Parses legacy SAP Print Workbench flat RDI lines."""
        lines = rdi_content.splitlines()

        header_dict: Dict[str, str] = {}
        power_periods: List[PowerPeriod] = []
        energy_periods: List[EnergyPeriod] = []
        power_total = Decimal("0.00")
        energy_total = Decimal("0.00")

        iee_rate = Decimal("0.051127")
        iee_amount = Decimal("0.00")
        bono_social = Decimal("0.00")
        meter_rental = Decimal("0.00")
        other_services = Decimal("0.00")
        tax_base = Decimal("0.00")
        iva_rate = Decimal("0.21")
        iva_amount = Decimal("0.00")
        total_amount = Decimal("0.00")
        spool_id = None
        print_timestamp = None
        form_name = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Tokens
            tokens = line.split()
            rec_type = tokens[0].upper()

            if rec_type == "H":  # Header line: H OPBEL 10000001 VKONT 2000000123 ...
                for i in range(1, len(tokens) - 1, 2):
                    header_dict[tokens[i].upper()] = tokens[i + 1]

            elif rec_type == "D":  # Data line: D POWER P1 4.60 30 0.082245 11.35
                sub_tag = tokens[1].upper() if len(tokens) > 1 else ""
                if sub_tag == "POWER":
                    # D POWER P1 4.600 30 0.082245 11.35
                    period = tokens[2]
                    kw = parse_spanish_decimal(tokens[3]) or Decimal("0.00")
                    days = int(tokens[4]) if tokens[4].isdigit() else 30
                    price = parse_spanish_decimal(tokens[5]) or Decimal("0.00")
                    tot = parse_spanish_decimal(tokens[6]) or Decimal("0.00")
                    power_periods.append(PowerPeriod(
                        period=period,
                        contracted_power_kw=kw,
                        days=days,
                        unit_price_eur_kw_day=price,
                        total_eur=tot
                    ))
                    power_total += tot
                elif sub_tag == "ENERGY":
                    # D ENERGY P1 120 0.185200 22.22
                    period = tokens[2]
                    kwh = parse_spanish_decimal(tokens[3]) or Decimal("0.00")
                    price = parse_spanish_decimal(tokens[4]) or Decimal("0.00")
                    tot = parse_spanish_decimal(tokens[5]) or Decimal("0.00")
                    energy_periods.append(EnergyPeriod(
                        period=period,
                        consumption_kwh=kwh,
                        unit_price_eur_kwh=price,
                        total_eur=tot
                    ))
                    energy_total += tot
                elif sub_tag == "IEE":
                    # D IEE 0.051127 66.09 3.38
                    iee_rate = parse_spanish_decimal(tokens[2]) or Decimal("0.051127")
                    iee_amount = parse_spanish_decimal(tokens[4]) if len(tokens) > 4 else parse_spanish_decimal(tokens[3])
                elif sub_tag == "BONO_SOCIAL":
                    bono_social = parse_spanish_decimal(tokens[-1]) or Decimal("0.00")
                elif sub_tag in ("ALQUILER", "EQUIPMENT"):
                    meter_rental = parse_spanish_decimal(tokens[-1]) or Decimal("0.00")
                elif sub_tag == "BASE_IMPONIBLE":
                    tax_base = parse_spanish_decimal(tokens[-1]) or Decimal("0.00")
                elif sub_tag == "IVA":
                    iva_rate = parse_spanish_decimal(tokens[2]) or Decimal("0.21")
                    iva_amount = parse_spanish_decimal(tokens[-1]) or Decimal("0.00")
                elif sub_tag in ("TOTAL_FACTURA", "TOTAL_AMNT"):
                    total_amount = parse_spanish_decimal(tokens[-1]) or Decimal("0.00")

            elif rec_type == "S":  # Spool line: S SPOOL_ID SPOOL_00018928 TIMESTAMP 15/11/2026 14:12:08
                for i in range(1, len(tokens) - 1, 2):
                    if tokens[i].upper() in ("SPOOL_ID", "SPOOLNO"):
                        spool_id = tokens[i + 1]
                    elif tokens[i].upper() == "TIMESTAMP":
                        print_timestamp = tokens[i + 1]
                    elif tokens[i].upper() == "FORM":
                        form_name = tokens[i + 1]

        invoice_number = header_dict.get("OPBEL", "UNKNOWN_INV")
        cups = header_dict.get("CUPS", "ES0000000000000000XX")
        vkont = header_dict.get("VKONT")
        gpart = header_dict.get("GPART")
        tariff_str = header_dict.get("TARIFF", "2.0TD")

        tariff = TariffType.TARIFF_20TD
        for t in TariffType:
            if t.value in tariff_str:
                tariff = t
                break

        return SpanishBillExtract(
            invoice_number=invoice_number,
            business_partner=gpart,
            contract_account=vkont,
            cups=cups,
            tariff=tariff,
            power_periods=power_periods,
            energy_periods=energy_periods,
            power_total=power_total,
            energy_total=energy_total,
            iee_base=power_total + energy_total,
            iee_rate=iee_rate,
            iee_amount=iee_amount or Decimal("0.00"),
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
            source_file=source_file
        )
