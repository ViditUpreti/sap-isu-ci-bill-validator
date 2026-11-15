"""
Pydantic Schemas for SAP IS-U / Convergent Invoicing (CI) Spanish Utility Billing.
Covers extracted PDF/XML bills, SAP Backend Tables (ERDK, DFKKINV_DOC_H/I, DFKKOP),
Mathematical Validation checks, and Reconciliation results.
"""

from pydantic import BaseModel, Field, field_validator, ConfigDict
from decimal import Decimal
from typing import List, Dict, Optional, Any, Union
from datetime import date
from enum import Enum


class TariffType(str, Enum):
    TARIFF_20TD = "2.0TD"
    TARIFF_30TD = "3.0TD"
    TARIFF_61TD = "6.1TD"
    TARIFF_RL1 = "RL.1"
    TARIFF_RL2 = "RL.2"
    TARIFF_RL3 = "RL.3"
    UNKNOWN = "UNKNOWN"


class DiffStatus(str, Enum):
    MATCH = "MATCH"
    DISCREPANCY = "DISCREPANCY"
    LAYOUT_SHIFT = "LAYOUT_SHIFT"
    MASKED = "MASKED"
    MISSING_BASELINE = "MISSING_BASELINE"
    MISSING_TARGET = "MISSING_TARGET"


class ReconcileStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    OUTSORTED = "OUTSORTED"
    WARNING = "WARNING"


class PowerPeriod(BaseModel):
    period: str = Field(..., description="Power period (P1, P2, P3, etc.)")
    contracted_power_kw: Decimal = Field(default=Decimal("0.00"), description="Contracted power in kW")
    days: int = Field(default=30, description="Billing days for period")
    unit_price_eur_kw_day: Decimal = Field(default=Decimal("0.000000"), description="Rate €/kW/day")
    total_eur: Decimal = Field(default=Decimal("0.00"), description="Calculated total for power period")


class EnergyPeriod(BaseModel):
    period: str = Field(..., description="Energy period (P1, P2, P3, etc.)")
    consumption_kwh: Decimal = Field(default=Decimal("0.00"), description="Consumption in kWh")
    unit_price_eur_kwh: Decimal = Field(default=Decimal("0.000000"), description="Unit price €/kWh")
    total_eur: Decimal = Field(default=Decimal("0.00"), description="Calculated total for energy period")


class InvoiceItem(BaseModel):
    item_id: int
    item_type: str = Field(..., description="POWER, ENERGY, IEE, EQUIPMENT, BONO_SOCIAL, IVA, OTHER")
    description: str
    base_amount: Decimal = Field(default=Decimal("0.00"))
    rate: Optional[Decimal] = None
    calculated_amount: Decimal = Field(default=Decimal("0.00"))
    sub_ap_id: Optional[str] = None
    tax_code: Optional[str] = None


class SpanishBillExtract(BaseModel):
    """Normalized structured extraction from Spanish PDF or XML/RDI invoice."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Identifiers & Header
    invoice_number: str = Field(..., description="Factura Nº (SAP OPBEL / Print Document)")
    fiscal_id: Optional[str] = Field(None, description="NIF/CIF of customer")
    business_partner: Optional[str] = Field(None, description="SAP GPART")
    contract_account: Optional[str] = Field(None, description="SAP VKONT")
    cups: str = Field(..., description="Código Unificado de Punto de Suministro")
    tariff: TariffType = Field(default=TariffType.TARIFF_20TD)
    issue_date: Optional[date] = None
    due_date: Optional[date] = None
    billing_period_start: Optional[date] = None
    billing_period_end: Optional[date] = None
    billing_days: int = Field(default=30)
    customer_name: Optional[str] = None
    supply_address: Optional[str] = None

    # Line Item Summaries
    power_periods: List[PowerPeriod] = Field(default_factory=list)
    energy_periods: List[EnergyPeriod] = Field(default_factory=list)

    power_total: Decimal = Field(default=Decimal("0.00"), description="Término de Potencia total")
    energy_total: Decimal = Field(default=Decimal("0.00"), description="Término de Energía total")

    # Regulatory & Tax Breakdown
    iee_base: Decimal = Field(default=Decimal("0.00"), description="Base Impuesto Eléctrico (Potencia + Energía)")
    iee_rate: Decimal = Field(default=Decimal("0.051127"), description="Tasa IEE aplicada")
    iee_amount: Decimal = Field(default=Decimal("0.00"), description="Importe Impuesto Eléctrico")

    bono_social_amount: Decimal = Field(default=Decimal("0.00"), description="Financiación Bono Social")
    meter_rental_amount: Decimal = Field(default=Decimal("0.00"), description="Alquiler de equipos de medida")
    other_services_amount: Decimal = Field(default=Decimal("0.00"), description="Mantenimiento / Otros")

    tax_base: Decimal = Field(default=Decimal("0.00"), description="Base Imponible (Suma neta sujeta a IVA)")
    iva_rate: Decimal = Field(default=Decimal("0.21"), description="Tasa de IVA (21%, 10%, 5%)")
    iva_amount: Decimal = Field(default=Decimal("0.00"), description="Importe IVA")

    total_amount: Decimal = Field(default=Decimal("0.00"), description="Total Factura € (Importe Total)")

    # Technical Spool & Layout Metadata
    spool_id: Optional[str] = Field(None, description="SAP Print Spool ID (e.g. SPOOL_00018928)")
    print_timestamp: Optional[str] = Field(None, description="Print run timestamp (dynamic)")
    form_name: Optional[str] = Field(None, description="SAP EFRM / SmartForm / ADS form name")
    qr_content: Optional[str] = Field(None, description="Decoded Verifactu / FacturaE QR payload")
    source_file: Optional[str] = None
    bounding_boxes: Dict[str, Any] = Field(default_factory=dict, description="Coordinates for visual diff")


# ==============================================================================
# SAP Backend Table Schemas
# ==============================================================================

class ERDK_Row(BaseModel):
    """SAP IS-U Print Document Header (Table ERDK)."""
    MANDT: str = "100"
    OPBEL: str = Field(..., description="Print Document Number")
    INVOICED_BILL: Optional[str] = Field(None, description="Billing Document Number (ERCH-BELNR)")
    BUKRS: str = Field(default="ES01", description="Company Code")
    TOTAL_AMNT: Decimal = Field(..., description="Total Gross Invoice Amount in EUR")
    WAERS: str = Field(default="EUR", description="Currency")
    VKONT: str = Field(..., description="Contract Account")
    GPART: str = Field(..., description="Business Partner")
    PRINTDATE: Optional[date] = None
    FORM: Optional[str] = Field(None, description="SmartForm or ADS Form Name")
    DRUCKSTATUS: Optional[str] = Field("3", description="Print Status: 1=Created, 2=Printed, 3=Archived")


class DFKKINV_DOC_H_Row(BaseModel):
    """SAP CI Invoicing Document Header (Table DFKKINV_DOC_H)."""
    MANDT: str = "100"
    INVDOCNO: str = Field(..., description="CI Invoicing Document Number")
    APPLK: str = Field(default="U", description="Application Area: U=Utilities")
    GPART: str = Field(..., description="Business Partner")
    VKONT: str = Field(..., description="Contract Account")
    TOTAL_AMNT: Decimal = Field(..., description="Total Invoicing Net Amount in EUR")
    INV_CURR: str = Field(default="EUR", description="Currency")
    INV_STATUS: str = Field(default="0", description="0=Active, 1=Reversed")
    CRDATE: Optional[date] = None


class DFKKINV_DOC_I_Row(BaseModel):
    """SAP CI Invoicing Document Items (Table DFKKINV_DOC_I)."""
    MANDT: str = "100"
    INVDOCNO: str = Field(..., description="CI Invoicing Document Number")
    INVDOCITEM: str = Field(..., description="Item Number (00001, 00002, etc.)")
    ITEMTYPE: str = Field(default="0EXT", description="Item Type: 0EXT=External billing, 0SUB=Subtotal")
    SUB_AP_ID: str = Field(..., description="Sub Application: POWER, ENERGY, IEE, EQUIPMENT, BONO_SOC, VAT")
    TAX_CODE: Optional[str] = Field(None, description="Tax Code: V1=21%, V0=5%, V2=10%, I1=IEE")
    BETRW: Decimal = Field(..., description="Amount in Document Currency")
    NETTO_AMNT: Optional[Decimal] = None
    TXDAT: Optional[date] = None


class DFKKOP_Row(BaseModel):
    """SAP FI-CA Contract Account Open Items (Table DFKKOP)."""
    MANDT: str = "100"
    OPBEL: str = Field(..., description="Document Number (Invoicing / Print Document)")
    OPUPK: str = Field(default="0001", description="Item Counter")
    BUKRS: str = Field(default="ES01", description="Company Code")
    VKONT: str = Field(..., description="Contract Account")
    GPART: str = Field(..., description="Business Partner")
    FAEDN: Optional[date] = Field(None, description="Due Date for Payment")
    BETRW: Decimal = Field(..., description="Open Item Amount (Gross)")
    STAKU: Optional[str] = Field("", description="Statistical Key")
    AUGST: Optional[str] = Field("", description="Clearing Status: ''=Open, 9=Cleared")
    HVORG: Optional[str] = Field("0100", description="Main Transaction (0100=Electricity Billing)")
    TVORG: Optional[str] = Field("0001", description="Sub Transaction (0001=Standard Consumption)")
    BLART: Optional[str] = Field("ED", description="Document Type (ED=Print Document)")


# ==============================================================================
# Validation & Reconciliation Result Schemas
# ==============================================================================

class FieldDiff(BaseModel):
    field_name: str
    baseline_value: Any
    target_value: Any
    difference: Optional[Any] = None
    tolerance_passed: bool = True
    status: DiffStatus = DiffStatus.MATCH
    message: str = ""
    bounding_box: Optional[List[float]] = None  # [x0, y0, x1, y1]


class VisualDiffResult(BaseModel):
    invoice_number: str
    page_number: int
    has_layout_shift: bool = False
    max_shift_pt: float = 0.0
    field_diffs: List[FieldDiff] = Field(default_factory=list)
    image_baseline_path: Optional[str] = None
    image_target_path: Optional[str] = None
    image_overlay_path: Optional[str] = None


class MathItemDetail(BaseModel):
    component: str
    formula: str
    extracted_value: Decimal
    recalculated_value: Decimal
    variance: Decimal
    tolerance_passed: bool
    status: str  # OK, DEFECT, OUTSORT
    regulation_reference: str


class MathValidationResult(BaseModel):
    invoice_number: str
    tariff: str
    all_passed: bool
    is_outsorted: bool = False
    outsort_reasons: List[str] = Field(default_factory=list)
    components: List[MathItemDetail] = Field(default_factory=list)
    total_extracted: Decimal
    total_recalculated: Decimal
    total_variance: Decimal


class ReconciliationRecord(BaseModel):
    """3-Way match outcome across PDF, Regulatory Math, and SAP Tables."""
    invoice_number: str
    cups: str
    contract_account: Optional[str] = None
    business_partner: Optional[str] = None
    tariff: str = "2.0TD"

    # Values
    bill_total: Decimal
    erdk_total: Optional[Decimal] = None
    inv_doc_h_total: Optional[Decimal] = None
    dfkkop_total: Optional[Decimal] = None

    # Variances
    var_bill_vs_erdk: Optional[Decimal] = None
    var_bill_vs_ci_h: Optional[Decimal] = None
    var_bill_vs_dfkkop: Optional[Decimal] = None

    # Validation Flags
    math_valid: bool = True
    erdk_matched: bool = True
    ci_items_matched: bool = True
    dfkkop_matched: bool = True
    visual_regression_passed: bool = True

    overall_status: ReconcileStatus = ReconcileStatus.PASS
    is_outsorted: bool = False
    outsort_reasons: List[str] = Field(default_factory=list)
    defect_details: List[str] = Field(default_factory=list)
    item_reconciliations: List[Dict[str, Any]] = Field(default_factory=list)


class BatchReconciliationSummary(BaseModel):
    timestamp: str
    total_invoices_tested: int = 0
    passed_count: int = 0
    failed_count: int = 0
    outsorted_count: int = 0
    warning_count: int = 0
    pass_rate_pct: float = 0.0
    total_billed_amount_eur: Decimal = Decimal("0.00")
    total_financial_variance_eur: Decimal = Decimal("0.00")
    tariff_breakdown: Dict[str, Dict[str, int]] = Field(default_factory=dict)
    records: List[ReconciliationRecord] = Field(default_factory=list)
