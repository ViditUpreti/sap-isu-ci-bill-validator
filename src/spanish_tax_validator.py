"""
Spanish Regulatory & Mathematical Validator for Utility Invoices.
Performs exact internal recalculations of Spanish electricity/gas tariff components
and taxes adhering strictly to CNMC circulars and BOE Royal Decrees:
- Término de Potencia (P1..P6)
- Término de Energía (P1..P6)
- Impuesto sobre la Electricidad (IEE: 5.1127%, 3.8%, 2.5%, 0.5% + minimum tax rules)
- Financiación Bono Social (Orden TED statutory daily fee)
- Alquiler de contador (CNMC regulated meter fee)
- Base Imponible & IVA (21%, 10%, 5%)
- SAP IS-U Outsorting simulation (Transaction EASIB / EA05)
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, List, Optional, Tuple

from src.models import (
    SpanishBillExtract, MathValidationResult, MathItemDetail, TariffType
)
from utils.locale_parser import format_spanish_currency


class SpanishRegulatoryValidator:
    """
    Mathematical verification engine for Spanish energy bills.
    Validates arithmetic accuracy, regulatory compliance, and outsort conditions.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.tolerances = self.config.get('tolerances', {})
        self.amount_tol = Decimal(str(self.tolerances.get('amount_tolerance_eur', 0.01)))
        self.outsort_thresh = Decimal(str(self.tolerances.get('outsorting_threshold_eur', 0.05)))

        taxes_cfg = self.config.get('taxes', {})
        self.valid_iva_rates = [Decimal(str(r)) for r in taxes_cfg.get('iva', {}).get('valid_rates', [0.05, 0.10, 0.21])]
        self.valid_iee_rates = [Decimal(str(r)) for r in taxes_cfg.get('iee', {}).get('valid_rates', [0.005, 0.025, 0.038, 0.0511269632, 0.0511])]
        self.min_iee_rate_mwh = Decimal(str(taxes_cfg.get('iee', {}).get('minimum_fee_per_mwh', 0.50)))

        reg_cfg = self.config.get('regulatory_fees', {})
        self.bono_daily_rate = Decimal(str(reg_cfg.get('bono_social', {}).get('daily_rate_eur', 0.006270)))
        self.meter_monthly_monophase = Decimal(str(reg_cfg.get('meter_rental', {}).get('monthly_rate_monophase_eur', 0.81)))
        self.meter_monthly_triphase = Decimal(str(reg_cfg.get('meter_rental', {}).get('monthly_rate_triphase_eur', 1.36)))

    def validate_bill(self, bill: SpanishBillExtract) -> MathValidationResult:
        """Runs full step-by-step mathematical recalculation against extracted bill."""
        components: List[MathItemDetail] = []
        outsort_reasons: List[str] = []

        # 1. Recalculate Power Term
        recalc_power_total, power_details = self._recalculate_power(bill)
        power_diff = abs(bill.power_total - recalc_power_total)
        power_pass = power_diff <= self.amount_tol
        components.append(MathItemDetail(
            component="Término de Potencia",
            formula=power_details,
            extracted_value=bill.power_total,
            recalculated_value=recalc_power_total,
            variance=bill.power_total - recalc_power_total,
            tolerance_passed=power_pass,
            status="OK" if power_pass else "DEFECT",
            regulation_reference="Circular 3/2020 CNMC & RD 148/2021"
        ))
        if power_diff > self.outsort_thresh:
            outsort_reasons.append(f"Discrepancia en Término de Potencia ({power_diff:.2f} € > {self.outsort_thresh:.2f} €)")

        # 2. Recalculate Energy Term
        recalc_energy_total, energy_details = self._recalculate_energy(bill)
        energy_diff = abs(bill.energy_total - recalc_energy_total)
        energy_pass = energy_diff <= self.amount_tol
        components.append(MathItemDetail(
            component="Término de Energía Activa",
            formula=energy_details,
            extracted_value=bill.energy_total,
            recalculated_value=recalc_energy_total,
            variance=bill.energy_total - recalc_energy_total,
            tolerance_passed=energy_pass,
            status="OK" if energy_pass else "DEFECT",
            regulation_reference="Circular 3/2020 CNMC & RD 148/2021"
        ))
        if energy_diff > self.outsort_thresh:
            outsort_reasons.append(f"Discrepancia en Término de Energía ({energy_diff:.2f} € > {self.outsort_thresh:.2f} €)")

        # 3. Recalculate IEE (Impuesto Eléctrico)
        recalc_iee_base = recalc_power_total + recalc_energy_total
        recalc_iee_amount, iee_details = self._recalculate_iee(bill, recalc_iee_base)
        iee_diff = abs(bill.iee_amount - recalc_iee_amount)
        iee_pass = iee_diff <= self.amount_tol
        components.append(MathItemDetail(
            component="Impuesto sobre Electricidad (IEE)",
            formula=iee_details,
            extracted_value=bill.iee_amount,
            recalculated_value=recalc_iee_amount,
            variance=bill.iee_amount - recalc_iee_amount,
            tolerance_passed=iee_pass,
            status="OK" if iee_pass else "DEFECT",
            regulation_reference="Ley 38/1992 Impuestos Especiales art. 64 ter"
        ))
        if iee_diff > self.outsort_thresh:
            outsort_reasons.append(f"Discrepancia en Impuesto Eléctrico IEE ({iee_diff:.2f} € > {self.outsort_thresh:.2f} €)")

        # Check IEE rate validity
        rate_matches = any(abs(bill.iee_rate - vr) < Decimal("0.0005") for vr in self.valid_iee_rates)
        if not rate_matches:
            outsort_reasons.append(f"Tasa IEE no reglamentaria detectada: {bill.iee_rate * Decimal('100'):.4f}%")

        # 4. Recalculate Bono Social
        recalc_bono = (Decimal(str(bill.billing_days)) * self.bono_daily_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        bono_diff = abs(bill.bono_social_amount - recalc_bono)
        bono_pass = bono_diff <= self.amount_tol
        components.append(MathItemDetail(
            component="Financiación Bono Social",
            formula=f"{bill.billing_days} días x {self.bono_daily_rate:.6f} €/día",
            extracted_value=bill.bono_social_amount,
            recalculated_value=recalc_bono,
            variance=bill.bono_social_amount - recalc_bono,
            tolerance_passed=bono_pass,
            status="OK" if bono_pass else "DEFECT",
            regulation_reference="RD 897/2017 & Orden TED/733/2022"
        ))

        # 5. Recalculate Alquiler de Contador
        # In Spain: Monophasic (2.0TD <= 15kW): 0.81 €/month; Triphasic (>15kW, 3.0TD, 6.1TD): 1.36 €/month
        is_triphase = (
            bill.tariff in (TariffType.TARIFF_30TD, TariffType.TARIFF_61TD) or
            bill.meter_rental_amount >= Decimal("1.20")
        )
        meter_monthly = self.meter_monthly_triphase if is_triphase else self.meter_monthly_monophase
        daily_meter_rate = (meter_monthly / Decimal("30")).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        recalc_meter = (Decimal(str(bill.billing_days)) * daily_meter_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if bill.meter_rental_amount == meter_monthly:
            recalc_meter = meter_monthly

        meter_diff = abs(bill.meter_rental_amount - recalc_meter)
        meter_pass = meter_diff <= self.amount_tol
        components.append(MathItemDetail(
            component="Alquiler Equipos de Medida",
            formula=f"{bill.billing_days} días x {daily_meter_rate:.6f} €/día",
            extracted_value=bill.meter_rental_amount,
            recalculated_value=recalc_meter,
            variance=bill.meter_rental_amount - recalc_meter,
            tolerance_passed=meter_pass,
            status="OK" if meter_pass else "DEFECT",
            regulation_reference="Orden ITC/3860/2007 CNMC"
        ))

        # 6. Recalculate Base Imponible
        recalc_tax_base = (
            recalc_power_total + recalc_energy_total + recalc_iee_amount +
            recalc_bono + recalc_meter + bill.other_services_amount
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        base_diff = abs(bill.tax_base - recalc_tax_base)
        base_pass = base_diff <= self.amount_tol
        components.append(MathItemDetail(
            component="Base Imponible",
            formula="Potencia + Energía + IEE + Bono Social + Alquiler + Otros",
            extracted_value=bill.tax_base,
            recalculated_value=recalc_tax_base,
            variance=bill.tax_base - recalc_tax_base,
            tolerance_passed=base_pass,
            status="OK" if base_pass else "DEFECT",
            regulation_reference="Ley 37/1992 del IVA (Base gravable)"
        ))
        if base_diff > self.outsort_thresh:
            outsort_reasons.append(f"Discrepancia en Base Imponible ({base_diff:.2f} € > {self.outsort_thresh:.2f} €)")

        # 7. Recalculate IVA
        iva_matches = any(abs(bill.iva_rate - vr) < Decimal("0.0005") for vr in self.valid_iva_rates)
        if not iva_matches:
            outsort_reasons.append(f"Tasa IVA no reconocida legalmente: {bill.iva_rate * Decimal('100'):.1f}%")

        recalc_iva_amount = (recalc_tax_base * bill.iva_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        iva_diff = abs(bill.iva_amount - recalc_iva_amount)
        iva_pass = iva_diff <= self.amount_tol
        components.append(MathItemDetail(
            component="Impuesto sobre el Valor Añadido (IVA)",
            formula=f"{bill.iva_rate * Decimal('100'):.1f}% s/ {recalc_tax_base:.2f} €",
            extracted_value=bill.iva_amount,
            recalculated_value=recalc_iva_amount,
            variance=bill.iva_amount - recalc_iva_amount,
            tolerance_passed=iva_pass,
            status="OK" if iva_pass else "DEFECT",
            regulation_reference="Ley 37/1992 del IVA art. 90/91"
        ))
        if iva_diff > self.outsort_thresh:
            outsort_reasons.append(f"Discrepancia en Importe IVA ({iva_diff:.2f} € > {self.outsort_thresh:.2f} €)")

        # 8. Recalculate Total Factura
        recalc_total = (recalc_tax_base + recalc_iva_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_diff = abs(bill.total_amount - recalc_total)
        total_pass = total_diff <= self.amount_tol
        components.append(MathItemDetail(
            component="Total Factura",
            formula=f"Base Imponible ({recalc_tax_base:.2f} €) + IVA ({recalc_iva_amount:.2f} €)",
            extracted_value=bill.total_amount,
            recalculated_value=recalc_total,
            variance=bill.total_amount - recalc_total,
            tolerance_passed=total_pass,
            status="OK" if total_pass else "DEFECT",
            regulation_reference="RD 148/2021 & Reglamento de Facturación RD 1619/2012"
        ))
        if total_diff > self.outsort_thresh:
            outsort_reasons.append(f"Discrepancia en Total Factura ({total_diff:.2f} € > {self.outsort_thresh:.2f} €)")

        all_passed = all(c.tolerance_passed for c in components)
        is_outsorted = len(outsort_reasons) > 0

        return MathValidationResult(
            invoice_number=bill.invoice_number,
            tariff=bill.tariff.value,
            all_passed=all_passed,
            is_outsorted=is_outsorted,
            outsort_reasons=outsort_reasons,
            components=components,
            total_extracted=bill.total_amount,
            total_recalculated=recalc_total,
            total_variance=bill.total_amount - recalc_total
        )

    def _recalculate_power(self, bill: SpanishBillExtract) -> Tuple[Decimal, str]:
        if not bill.power_periods:
            return bill.power_total, "Sin desglose de periodos de potencia"

        total = Decimal("0.00")
        details = []
        for p in bill.power_periods:
            term = (p.contracted_power_kw * Decimal(str(p.days)) * p.unit_price_eur_kw_day).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total += term
            details.append(f"{p.period}: {p.contracted_power_kw:.3f} kW x {p.days}d x {p.unit_price_eur_kw_day:.6f} € = {term:.2f} €")

        return total, "; ".join(details)

    def _recalculate_energy(self, bill: SpanishBillExtract) -> Tuple[Decimal, str]:
        if not bill.energy_periods:
            return bill.energy_total, "Sin desglose de periodos de energía"

        total = Decimal("0.00")
        details = []
        for e in bill.energy_periods:
            term = (e.consumption_kwh * e.unit_price_eur_kwh).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total += term
            details.append(f"{e.period}: {e.consumption_kwh:.1f} kWh x {e.unit_price_eur_kwh:.6f} € = {term:.2f} €")

        return total, "; ".join(details)

    def _recalculate_iee(self, bill: SpanishBillExtract, base: Decimal) -> Tuple[Decimal, str]:
        amt = (base * bill.iee_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Minimum tax check per MWh
        total_kwh = sum((e.consumption_kwh for e in bill.energy_periods), Decimal("0.00"))
        if total_kwh > 0:
            min_fee = ((total_kwh / Decimal("1000")) * self.min_iee_rate_mwh).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if amt < min_fee:
                amt = min_fee
                return amt, f"Aplicada cuota mínima legal IEE: {self.min_iee_rate_mwh} €/MWh = {min_fee:.2f} €"

        return amt, f"{bill.iee_rate * Decimal('100'):.4f}% s/ {base:.2f} €"
