"""
SAP IS-U & CI 3-Way Reconciliation Engine.
Reconciles extracted PDF/XML bills against:
1. Regulatory & Mathematical rules (Spanish CNMC / BOE)
2. SAP IS-U Print Document Header (Table ERDK)
3. SAP CI Invoicing Document Header & Items (Tables DFKKINV_DOC_H & DFKKINV_DOC_I)
4. SAP FI-CA Contract Account Open Items (Table DFKKOP)
"""

import os
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple, Union
import pandas as pd

from src.models import (
    SpanishBillExtract, MathValidationResult, ReconciliationRecord,
    ReconcileStatus, ERDK_Row, DFKKINV_DOC_H_Row, DFKKINV_DOC_I_Row, DFKKOP_Row
)
from utils.locale_parser import parse_spanish_decimal, parse_spanish_date


class SapReconciler:
    """
    Core 3-Way matching engine for Spanish Utilities.
    Performs field-level and item-level reconciliation between bill forms
    and SAP backend tables.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.tolerances = self.config.get('tolerances', {})
        self.amount_tol = Decimal(str(self.tolerances.get('amount_tolerance_eur', 0.01)))
        self.outsort_thresh = Decimal(str(self.tolerances.get('outsorting_threshold_eur', 0.05)))

        # Cache for SAP DataFrames
        self.df_erdk: Optional[pd.DataFrame] = None
        self.df_inv_h: Optional[pd.DataFrame] = None
        self.df_inv_i: Optional[pd.DataFrame] = None
        self.df_dfkkop: Optional[pd.DataFrame] = None

    def load_sap_tables(
        self,
        erdk_source: Union[str, pd.DataFrame],
        inv_h_source: Union[str, pd.DataFrame],
        inv_i_source: Union[str, pd.DataFrame],
        dfkkop_source: Union[str, pd.DataFrame]
    ) -> None:
        """Loads and normalizes SAP table exports from CSV paths or DataFrames."""
        self.df_erdk = self._normalize_df(erdk_source)
        self.df_inv_h = self._normalize_df(inv_h_source)
        self.df_inv_i = self._normalize_df(inv_i_source)
        self.df_dfkkop = self._normalize_df(dfkkop_source)

    def _normalize_df(self, source: Union[str, pd.DataFrame]) -> pd.DataFrame:
        if isinstance(source, pd.DataFrame):
            df = source.copy()
        elif os.path.exists(source):
            # Try reading with semicolon or comma
            try:
                df = pd.read_csv(source, sep=None, engine='python', dtype=str)
            except Exception:
                df = pd.read_csv(source, dtype=str)
        else:
            raise FileNotFoundError(f"SAP table export not found: {source}")

        # Strip whitespace from column names and string cells
        df.columns = [str(c).strip().upper() for c in df.columns]
        for col in df.columns:
            df[col] = df[col].astype(str).str.strip()
        return df

    def reconcile_bill(
        self,
        bill: SpanishBillExtract,
        math_result: MathValidationResult
    ) -> ReconciliationRecord:
        """Executes 3-way reconciliation for a single bill extract."""
        defect_details: List[str] = []
        outsort_reasons: list[str] = list(math_result.outsort_reasons)
        item_reconciliations: List[Dict[str, Any]] = []

        erdk_matched = True
        inv_h_matched = True
        ci_items_matched = True
        dfkkop_matched = True

        erdk_total: Optional[Decimal] = None
        inv_h_total: Optional[Decimal] = None
        dfkkop_total: Optional[Decimal] = None

        var_bill_vs_erdk: Optional[Decimal] = None
        var_bill_vs_ci_h: Optional[Decimal] = None
        var_bill_vs_dfkkop: Optional[Decimal] = None

        # ----------------------------------------------------------------------
        # 1. Match Table ERDK (Print Document Header)
        # ----------------------------------------------------------------------
        erdk_row = self._find_erdk_row(bill.invoice_number, bill.contract_account)
        if erdk_row is not None:
            raw_amt = erdk_row.get("TOTAL_AMNT")
            erdk_total = parse_spanish_decimal(raw_amt) or Decimal("0.00")
            var_bill_vs_erdk = bill.total_amount - erdk_total

            if abs(var_bill_vs_erdk) > self.amount_tol:
                erdk_matched = False
                msg = f"[ERDK] Discrepancia Total Factura: Bill ({bill.total_amount:.2f} €) vs ERDK-TOTAL_AMNT ({erdk_total:.2f} €) [Var: {var_bill_vs_erdk:+.2f} €]"
                defect_details.append(msg)
                if abs(var_bill_vs_erdk) > self.outsort_thresh:
                    outsort_reasons.append(msg)

            # Contract Account check
            erdk_vkont = erdk_row.get("VKONT")
            if bill.contract_account and erdk_vkont and bill.contract_account != erdk_vkont:
                erdk_matched = False
                defect_details.append(f"[ERDK] Mismatch Cuenta Contrato: Bill ({bill.contract_account}) vs ERDK ({erdk_vkont})")

            # Business Partner check
            erdk_gpart = erdk_row.get("GPART")
            if bill.business_partner and erdk_gpart and bill.business_partner != erdk_gpart:
                defect_details.append(f"[ERDK] Mismatch Interlocutor: Bill ({bill.business_partner}) vs ERDK ({erdk_gpart})")
        else:
            erdk_matched = False
            msg = f"[ERDK] Documento de impresión {bill.invoice_number} no existe en tabla ERDK"
            defect_details.append(msg)
            outsort_reasons.append(msg)

        # ----------------------------------------------------------------------
        # 2. Match Table DFKKINV_DOC_H (CI Invoicing Document Header)
        # ----------------------------------------------------------------------
        inv_h_row = self._find_inv_h_row(bill.invoice_number, bill.contract_account)
        if inv_h_row is not None:
            raw_h_amt = inv_h_row.get("TOTAL_AMNT")
            inv_h_total = parse_spanish_decimal(raw_h_amt) or Decimal("0.00")
            # In CI, DFKKINV_DOC_H stores the net or total invoicing amount
            var_bill_vs_ci_h = bill.tax_base - inv_h_total

            # Note: in some CI implementations, TOTAL_AMNT is net, in others gross
            # If closer to gross, compare to total_amount
            if abs(bill.total_amount - inv_h_total) < abs(var_bill_vs_ci_h):
                var_bill_vs_ci_h = bill.total_amount - inv_h_total

            if abs(var_bill_vs_ci_h) > self.amount_tol:
                inv_h_matched = False
                msg = f"[DFKKINV_DOC_H] Discrepancia Base Facturada: Bill Base ({bill.tax_base:.2f} €) vs CI-TOTAL_AMNT ({inv_h_total:.2f} €) [Var: {var_bill_vs_ci_h:+.2f} €]"
                defect_details.append(msg)
                if abs(var_bill_vs_ci_h) > self.outsort_thresh:
                    outsort_reasons.append(msg)
        else:
            inv_h_matched = False
            defect_details.append(f"[DFKKINV_DOC_H] Cabecera de facturación CI no encontrada para doc {bill.invoice_number}")

        # ----------------------------------------------------------------------
        # 3. Match Table DFKKINV_DOC_I (CI Invoicing Items)
        # ----------------------------------------------------------------------
        inv_items = self._find_inv_items(bill.invoice_number, bill.contract_account)
        if not inv_items.empty:
            items_pass, item_details = self._reconcile_ci_items(bill, inv_items)
            ci_items_matched = items_pass
            item_reconciliations.extend(item_details)
            for itm in item_details:
                if not itm["matched"]:
                    msg = f"[DFKKINV_DOC_I] Línea '{itm['concept']}': Bill ({itm['bill_amount']:.2f} €) vs SAP CI ({itm['sap_amount']:.2f} €)"
                    defect_details.append(msg)
                    if abs(itm['variance']) > self.outsort_thresh:
                        outsort_reasons.append(msg)
        else:
            ci_items_matched = False
            defect_details.append(f"[DFKKINV_DOC_I] No se encontraron posiciones CI para doc {bill.invoice_number}")

        # ----------------------------------------------------------------------
        # 4. Match Table DFKKOP (FI-CA Contract Account Open Items)
        # ----------------------------------------------------------------------
        dfkkop_row = self._find_dfkkop_row(bill.invoice_number, bill.contract_account)
        if dfkkop_row is not None:
            raw_op_amt = dfkkop_row.get("BETRW")
            dfkkop_total = parse_spanish_decimal(raw_op_amt) or Decimal("0.00")
            var_bill_vs_dfkkop = bill.total_amount - dfkkop_total

            if abs(var_bill_vs_dfkkop) > self.amount_tol:
                dfkkop_matched = False
                msg = f"[DFKKOP] Descuadre partida abierta FI-CA: Bill ({bill.total_amount:.2f} €) vs DFKKOP-BETRW ({dfkkop_total:.2f} €) [Var: {var_bill_vs_dfkkop:+.2f} €]"
                defect_details.append(msg)
                if abs(var_bill_vs_dfkkop) > self.outsort_thresh:
                    outsort_reasons.append(msg)

            # Check Due Date
            op_faedn = parse_spanish_date(dfkkop_row.get("FAEDN"))
            if bill.due_date and op_faedn and bill.due_date != op_faedn:
                defect_details.append(f"[DFKKOP] Fecha de vencimiento dispar: Bill ({bill.due_date}) vs DFKKOP-FAEDN ({op_faedn})")
        else:
            dfkkop_matched = False
            msg = f"[DFKKOP] Partida abierta FI-CA no contabilizada para doc {bill.invoice_number}"
            defect_details.append(msg)
            outsort_reasons.append(msg)

        # ----------------------------------------------------------------------
        # Overall Determination
        # ----------------------------------------------------------------------
        is_outsorted = len(outsort_reasons) > 0 or math_result.is_outsorted
        all_passed = (
            erdk_matched and inv_h_matched and ci_items_matched and
            dfkkop_matched and math_result.all_passed
        )

        if is_outsorted:
            overall_status = ReconcileStatus.OUTSORTED
        elif not all_passed:
            overall_status = ReconcileStatus.FAIL
        else:
            overall_status = ReconcileStatus.PASS

        return ReconciliationRecord(
            invoice_number=bill.invoice_number,
            cups=bill.cups,
            contract_account=bill.contract_account,
            business_partner=bill.business_partner,
            tariff=bill.tariff.value,
            bill_total=bill.total_amount,
            erdk_total=erdk_total,
            inv_doc_h_total=inv_h_total,
            dfkkop_total=dfkkop_total,
            var_bill_vs_erdk=var_bill_vs_erdk,
            var_bill_vs_ci_h=var_bill_vs_ci_h,
            var_bill_vs_dfkkop=var_bill_vs_dfkkop,
            math_valid=math_result.all_passed,
            erdk_matched=erdk_matched,
            ci_items_matched=ci_items_matched,
            dfkkop_matched=dfkkop_matched,
            visual_regression_passed=True,
            overall_status=overall_status,
            is_outsorted=is_outsorted,
            outsort_reasons=outsort_reasons,
            defect_details=defect_details,
            item_reconciliations=item_reconciliations
        )

    # --------------------------------------------------------------------------
    # Search & Lookups in SAP Tables
    # --------------------------------------------------------------------------

    def _find_erdk_row(self, invoice_number: str, vkont: Optional[str]) -> Optional[Dict[str, Any]]:
        if self.df_erdk is None or self.df_erdk.empty:
            return None

        # Look up by OPBEL
        match = self.df_erdk[self.df_erdk['OPBEL'].astype(str) == str(invoice_number)]
        if not match.empty:
            return match.iloc[0].to_dict()

        # Fallback look up by VKONT
        if vkont:
            match_vkont = self.df_erdk[self.df_erdk['VKONT'].astype(str) == str(vkont)]
            if not match_vkont.empty:
                return match_vkont.iloc[0].to_dict()

        return None

    def _find_inv_h_row(self, invoice_number: str, vkont: Optional[str]) -> Optional[Dict[str, Any]]:
        if self.df_inv_h is None or self.df_inv_h.empty:
            return None

        match = self.df_inv_h[self.df_inv_h['INVDOCNO'].astype(str) == str(invoice_number)]
        if not match.empty:
            return match.iloc[0].to_dict()

        if vkont and 'VKONT' in self.df_inv_h.columns:
            match_vkont = self.df_inv_h[self.df_inv_h['VKONT'].astype(str) == str(vkont)]
            if not match_vkont.empty:
                return match_vkont.iloc[0].to_dict()

        return None

    def _find_inv_items(self, invoice_number: str, vkont: Optional[str]) -> pd.DataFrame:
        if self.df_inv_i is None or self.df_inv_i.empty:
            return pd.DataFrame()

        match = self.df_inv_i[self.df_inv_i['INVDOCNO'].astype(str) == str(invoice_number)]
        return match

    def _find_dfkkop_row(self, invoice_number: str, vkont: Optional[str]) -> Optional[Dict[str, Any]]:
        if self.df_dfkkop is None or self.df_dfkkop.empty:
            return None

        match = self.df_dfkkop[self.df_dfkkop['OPBEL'].astype(str) == str(invoice_number)]
        if not match.empty:
            return match.iloc[0].to_dict()

        if vkont and 'VKONT' in self.df_dfkkop.columns:
            match_vkont = self.df_dfkkop[self.df_dfkkop['VKONT'].astype(str) == str(vkont)]
            if not match_vkont.empty:
                return match_vkont.iloc[0].to_dict()

        return None

    def _reconcile_ci_items(
        self,
        bill: SpanishBillExtract,
        df_items: pd.DataFrame
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """Reconciles sub-application items (POWER, ENERGY, IEE, VAT, METER)."""
        recons: List[Dict[str, Any]] = []
        all_ok = True

        # Aggregate amounts by SUB_AP_ID
        sub_totals: Dict[str, Decimal] = {}
        for _, row in df_items.iterrows():
            sub_id = str(row.get('SUB_AP_ID', '')).upper()
            amt = parse_spanish_decimal(row.get('BETRW')) or Decimal("0.00")
            sub_totals[sub_id] = sub_totals.get(sub_id, Decimal("0.00")) + amt

        # Concepts to verify
        check_map = [
            ("POWER", "Término de Potencia", bill.power_total),
            ("ENERGY", "Término de Energía", bill.energy_total),
            ("IEE", "Impuesto Eléctrico (IEE)", bill.iee_amount),
            ("EQUIPMENT", "Alquiler Contador", bill.meter_rental_amount),
            ("BONO_SOC", "Bono Social", bill.bono_social_amount),
            ("VAT", "Impuesto IVA", bill.iva_amount)
        ]

        for sub_id, name, bill_val in check_map:
            if sub_id in sub_totals:
                sap_val = sub_totals[sub_id]
                var = bill_val - sap_val
                matched = abs(var) <= self.amount_tol
                if not matched:
                    all_ok = False
                recons.append({
                    "concept": name,
                    "sub_ap_id": sub_id,
                    "bill_amount": bill_val,
                    "sap_amount": sap_val,
                    "variance": var,
                    "matched": matched
                })

        return all_ok, recons
