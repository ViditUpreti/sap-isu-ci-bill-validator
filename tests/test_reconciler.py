"""
Pytest Test Suite for SAP IS-U & CI Invoice Validation Suite (Spain Utility Edition).
Covers Spanish locale parsing, 2.0TD/3.0TD mathematical recalculations,
rounding & outsorting edge cases, PDF/XML extraction, and SAP 3-way table reconciliation.
"""

import os
import pytest
from decimal import Decimal
from datetime import date
import yaml

from utils.locale_parser import (
    parse_spanish_decimal, format_spanish_currency, parse_spanish_date
)
from src.models import (
    SpanishBillExtract, TariffType, PowerPeriod, EnergyPeriod,
    ReconcileStatus, ERDK_Row, DFKKOP_Row
)
from src.spanish_tax_validator import SpanishRegulatoryValidator
from src.sap_reconciler import SapReconciler
from src.pdf_extractor import SpanishBillPDFExtractor
from src.xml_rdi_parser import SAPXmlRdiParser
from src.visual_diff import VisualDiffEngine
from src.report_generator import ReportGenerator
from utils.sample_data_generator import SampleDataGenerator


@pytest.fixture(scope="session")
def base_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session")
def config(base_dir):
    cfg_path = os.path.join(base_dir, "config", "spanish_tariff_rules.yaml")
    with open(cfg_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def sample_dataset(base_dir):
    gen = SampleDataGenerator(base_dir)
    return gen.generate_all()


# ==============================================================================
# 1. Spanish Locale Parsing Tests
# ==============================================================================

class TestSpanishLocaleParser:
    def test_parse_spanish_currency_string(self):
        assert parse_spanish_decimal("1.234,56 €") == Decimal("1234.56")
        assert parse_spanish_decimal("85,27 €") == Decimal("85.27")
        assert parse_spanish_decimal("-12,50 €") == Decimal("-12.50")
        assert parse_spanish_decimal("0,082245 €/kW/día") == Decimal("0.082245")
        assert parse_spanish_decimal("  45,00 EUR ") == Decimal("45.00")
        assert parse_spanish_decimal(None) is None
        assert parse_spanish_decimal("") is None

    def test_format_spanish_currency(self):
        assert format_spanish_currency(Decimal("1234.56")) == "1.234,56 €"
        assert format_spanish_currency(Decimal("85.20")) == "85,20 €"
        assert format_spanish_currency(Decimal("0.082245"), decimals=6, show_symbol=False) == "0,082245"
        assert format_spanish_currency(None) == "0,00 €"

    def test_parse_spanish_date(self):
        assert parse_spanish_date("15/11/2026") == date(2026, 11, 15)
        assert parse_spanish_date("15-11-2026") == date(2026, 11, 15)
        assert parse_spanish_date("2026-11-15") == date(2026, 11, 15)
        assert parse_spanish_date("15 de noviembre de 2026") == date(2026, 11, 15)


# ==============================================================================
# 2. Spanish Regulatory & Mathematical Validation Tests
# ==============================================================================

class TestSpanishTaxValidator:
    def test_tariff_20td_exact_math(self, config):
        validator = SpanishRegulatoryValidator(config)
        bill = SpanishBillExtract(
            invoice_number="10000001",
            cups="ES0021000000000001AB",
            tariff=TariffType.TARIFF_20TD,
            billing_days=30,
            power_periods=[
                PowerPeriod(period="P1", contracted_power_kw=Decimal("4.600"), days=30, unit_price_eur_kw_day=Decimal("0.082245"), total_eur=Decimal("11.35")),
                PowerPeriod(period="P2", contracted_power_kw=Decimal("4.600"), days=30, unit_price_eur_kw_day=Decimal("0.015320"), total_eur=Decimal("2.11"))
            ],
            energy_periods=[
                EnergyPeriod(period="P1", consumption_kwh=Decimal("120.0"), unit_price_eur_kwh=Decimal("0.185200"), total_eur=Decimal("22.22")),
                EnergyPeriod(period="P2", consumption_kwh=Decimal("110.0"), unit_price_eur_kwh=Decimal("0.142100"), total_eur=Decimal("15.63")),
                EnergyPeriod(period="P3", consumption_kwh=Decimal("150.0"), unit_price_eur_kwh=Decimal("0.098500"), total_eur=Decimal("14.78"))
            ],
            power_total=Decimal("13.46"),
            energy_total=Decimal("52.63"),
            iee_base=Decimal("66.09"),
            iee_rate=Decimal("0.051127"),
            iee_amount=Decimal("3.38"),
            bono_social_amount=Decimal("0.19"),
            meter_rental_amount=Decimal("0.81"),
            tax_base=Decimal("70.47"),
            iva_rate=Decimal("0.21"),
            iva_amount=Decimal("14.80"),
            total_amount=Decimal("85.27")
        )

        res = validator.validate_bill(bill)
        assert res.all_passed is True
        assert res.is_outsorted is False
        assert len(res.outsort_reasons) == 0
        assert res.total_variance == Decimal("0.00")

    def test_rounding_edge_case_within_tolerance(self, config):
        """0.01 EUR rounding variance must pass under standard tolerance."""
        validator = SpanishRegulatoryValidator(config)
        bill = SpanishBillExtract(
            invoice_number="ROUND_01",
            cups="ES0021000000000001AB",
            tariff=TariffType.TARIFF_20TD,
            billing_days=30,
            power_total=Decimal("13.46"),
            energy_total=Decimal("52.63"),
            iee_amount=Decimal("3.38"),
            bono_social_amount=Decimal("0.19"),
            meter_rental_amount=Decimal("0.81"),
            tax_base=Decimal("70.47"),
            iva_rate=Decimal("0.21"),
            iva_amount=Decimal("14.80"),
            total_amount=Decimal("85.28")  # +0.01 € difference
        )
        res = validator.validate_bill(bill)
        assert res.all_passed is True
        assert res.is_outsorted is False

    def test_outsorting_trigger_on_large_discrepancy(self, config):
        """Variance > 0.05 EUR must trigger IS-U EASIB outsort status."""
        validator = SpanishRegulatoryValidator(config)
        bill = SpanishBillExtract(
            invoice_number="OUTSORT_01",
            cups="ES0021000000000001AB",
            tariff=TariffType.TARIFF_20TD,
            billing_days=30,
            power_total=Decimal("13.46"),
            energy_total=Decimal("52.63"),
            iee_amount=Decimal("3.38"),
            bono_social_amount=Decimal("0.19"),
            meter_rental_amount=Decimal("0.81"),
            tax_base=Decimal("70.47"),
            iva_rate=Decimal("0.21"),
            iva_amount=Decimal("14.80"),
            total_amount=Decimal("86.50")  # +1.23 € discrepancy
        )
        res = validator.validate_bill(bill)
        assert res.all_passed is False
        assert res.is_outsorted is True
        assert any("Total Factura" in r for r in res.outsort_reasons)

    def test_invalid_tax_rate_rejection(self, config):
        """Non-regulatory tax rate (e.g. IEE 1.50%) must trigger outsorting."""
        validator = SpanishRegulatoryValidator(config)
        bill = SpanishBillExtract(
            invoice_number="TAX_ERR_01",
            cups="ES0021000000000001AB",
            tariff=TariffType.TARIFF_20TD,
            billing_days=30,
            power_total=Decimal("13.46"),
            energy_total=Decimal("52.63"),
            iee_rate=Decimal("0.0150"),  # 1.5% is invalid in Spain
            iee_amount=Decimal("0.99"),
            tax_base=Decimal("68.08"),
            iva_rate=Decimal("0.21"),
            iva_amount=Decimal("14.30"),
            total_amount=Decimal("82.38")
        )
        res = validator.validate_bill(bill)
        assert res.is_outsorted is True
        assert any("Tasa IEE" in r for r in res.outsort_reasons)


# ==============================================================================
# 3. PDF & XML Parser Tests
# ==============================================================================

class TestParsers:
    def test_pdf_extraction_baseline(self, base_dir, config, sample_dataset):
        extractor = SpanishBillPDFExtractor(config)
        pdf_path = os.path.join(base_dir, "data", "baseline", "10000001_baseline.pdf")
        bill = extractor.extract(pdf_path)

        assert bill.invoice_number == "10000001"
        assert bill.cups == "ES0021000000000001AB"
        assert bill.tariff == TariffType.TARIFF_20TD
        assert bill.total_amount == Decimal("85.27")
        assert len(bill.power_periods) == 2
        assert len(bill.energy_periods) == 3
        assert bill.spool_id is not None

    def test_xml_spool_parsing(self, base_dir, config, sample_dataset):
        parser = SAPXmlRdiParser(config)
        xml_path = os.path.join(base_dir, "data", "target", "10000001_spool.xml")
        bill = parser.parse_file(xml_path)

        assert bill.invoice_number == "10000001"
        assert bill.cups == "ES0021000000000001AB"
        assert bill.total_amount == Decimal("85.27")
        assert len(bill.power_periods) == 2
        assert len(bill.energy_periods) == 3


# ==============================================================================
# 4. SAP 3-Way Reconciliation Tests
# ==============================================================================

class TestSapReconciliation:
    def test_clean_3way_match(self, base_dir, config, sample_dataset):
        reconciler = SapReconciler(config)
        sap_dir = os.path.join(base_dir, "data", "sap_tables")
        reconciler.load_sap_tables(
            os.path.join(sap_dir, "ERDK.csv"),
            os.path.join(sap_dir, "DFKKINV_DOC_H.csv"),
            os.path.join(sap_dir, "DFKKINV_DOC_I.csv"),
            os.path.join(sap_dir, "DFKKOP.csv")
        )

        extractor = SpanishBillPDFExtractor(config)
        validator = SpanishRegulatoryValidator(config)
        pdf_path = os.path.join(base_dir, "data", "baseline", "10000001_baseline.pdf")

        bill = extractor.extract(pdf_path)
        m_res = validator.validate_bill(bill)
        rec = reconciler.reconcile_bill(bill, m_res)

        assert rec.overall_status == ReconcileStatus.PASS
        assert rec.erdk_matched is True
        assert rec.dfkkop_matched is True
        assert rec.is_outsorted is False

    def test_fica_posting_imbalance_detection(self, base_dir, config, sample_dataset):
        """Bill 10000008 has intentional DFKKOP imbalance (82.00 vs 85.27)."""
        reconciler = SapReconciler(config)
        sap_dir = os.path.join(base_dir, "data", "sap_tables")
        reconciler.load_sap_tables(
            os.path.join(sap_dir, "ERDK.csv"),
            os.path.join(sap_dir, "DFKKINV_DOC_H.csv"),
            os.path.join(sap_dir, "DFKKINV_DOC_I.csv"),
            os.path.join(sap_dir, "DFKKOP.csv")
        )

        extractor = SpanishBillPDFExtractor(config)
        validator = SpanishRegulatoryValidator(config)
        pdf_path = os.path.join(base_dir, "data", "target", "10000008_target.pdf")

        bill = extractor.extract(pdf_path)
        m_res = validator.validate_bill(bill)
        rec = reconciler.reconcile_bill(bill, m_res)

        assert rec.dfkkop_matched is False
        assert rec.overall_status == ReconcileStatus.OUTSORTED
        assert any("DFKKOP" in d for d in rec.defect_details)


# ==============================================================================
# 5. Visual Diff & Layout Shift Tests
# ==============================================================================

class TestVisualDiff:
    def test_dynamic_masking_ignores_spool(self, config):
        engine = VisualDiffEngine(config)
        assert engine.is_dynamic_masked("SAP Spool: SPOOL_00018928") is True
        assert engine.is_dynamic_masked("Fecha Impresión: 15/11/2026") is True
        assert engine.is_dynamic_masked("Página 1 de 1") is True
        assert engine.is_dynamic_masked("Total Factura: 85,27 €") is False

    def test_layout_shift_detection(self, base_dir, config, sample_dataset):
        """Bill 10000007 has intentional 25pt vertical displacement."""
        engine = VisualDiffEngine(config)
        out_dir = os.path.join(base_dir, "reports", "test_diffs")
        base_pdf = os.path.join(base_dir, "data", "baseline", "10000007_baseline.pdf")
        targ_pdf = os.path.join(base_dir, "data", "target", "10000007_target.pdf")

        results = engine.compare_invoices(base_pdf, targ_pdf, out_dir, invoice_number="10000007")
        assert len(results) > 0
        assert results[0].has_layout_shift is True
        assert results[0].max_shift_pt >= 20.0
