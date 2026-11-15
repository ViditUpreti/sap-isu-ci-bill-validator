"""
Headless CLI Runner for SAP IS-U & CI Invoice Validation Suite (Spain Utility Edition).
Enables automated batch SIT/UAT regression testing and CI/CD pipeline integration.
"""

import os
import sys
import argparse
from decimal import Decimal
from datetime import datetime
import yaml

from src.pdf_extractor import SpanishBillPDFExtractor
from src.xml_rdi_parser import SAPXmlRdiParser
from src.spanish_tax_validator import SpanishRegulatoryValidator
from src.sap_reconciler import SapReconciler
from src.visual_diff import VisualDiffEngine
from src.report_generator import ReportGenerator
from src.models import (
    BatchReconciliationSummary, ReconcileStatus, MathValidationResult,
    ReconciliationRecord
)
from utils.locale_parser import format_spanish_currency
from utils.sample_data_generator import SampleDataGenerator


def parse_args():
    parser = argparse.ArgumentParser(
        description="SAP IS-U & CI Automated Bill Print & Invoice Validation Suite (Spain Utility Edition)"
    )
    parser.add_argument(
        "--baseline-dir",
        default="data/baseline",
        help="Directory containing baseline PDF invoices"
    )
    parser.add_argument(
        "--target-dir",
        default="data/target",
        help="Directory containing target PDF or XML/RDI invoices to test"
    )
    parser.add_argument(
        "--sap-tables-dir",
        default="data/sap_tables",
        help="Directory containing SAP CSV exports (ERDK.csv, DFKKINV_DOC_H.csv, DFKKINV_DOC_I.csv, DFKKOP.csv)"
    )
    parser.add_argument(
        "--config",
        default="config/spanish_tariff_rules.yaml",
        help="Path to YAML configuration file"
    )
    parser.add_argument(
        "--output-dir",
        default="reports",
        help="Directory where Excel and HTML audit reports are saved"
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=None,
        help="Override rounding tolerance in EUR (default: from config, e.g. 0.01)"
    )
    parser.add_argument(
        "--visual-diff",
        action="store_true",
        default=True,
        help="Generate visual pixel & bounding-box overlays (default: True)"
    )
    parser.add_argument(
        "--generate-samples",
        action="store_true",
        help="Generate synthetic Spanish utility dataset before running"
    )
    parser.add_argument(
        "--exit-code-on-fail",
        action="store_true",
        help="Return exit code 1 if any invoice is outsorted or fails reconciliation"
    )
    return parser.parse_args()


def main():
    args = parse_args()
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # Resolve relative paths
    config_path = os.path.join(base_dir, args.config) if not os.path.isabs(args.config) else args.config
    baseline_dir = os.path.join(base_dir, args.baseline_dir) if not os.path.isabs(args.baseline_dir) else args.baseline_dir
    target_dir = os.path.join(base_dir, args.target_dir) if not os.path.isabs(args.target_dir) else args.target_dir
    sap_dir = os.path.join(base_dir, args.sap_tables_dir) if not os.path.isabs(args.sap_tables_dir) else args.sap_tables_dir
    output_dir = os.path.join(base_dir, args.output_dir) if not os.path.isabs(args.output_dir) else args.output_dir

    if args.generate_samples or not os.path.exists(target_dir):
        print("\n[INFO] Generating synthetic Spanish utility test dataset...")
        gen = SampleDataGenerator(base_dir)
        gen.generate_all()
        print("[INFO] Sample dataset ready.\n")

    # Load Config
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if args.tolerance is not None:
        config.setdefault('tolerances', {})['amount_tolerance_eur'] = args.tolerance

    print("=" * 80)
    print("  SAP IS-U & CI Automated Bill Print & Invoice Validation Suite")
    print("  Edition: Spain Utility Edition (CNMC / BOE Regulatory Rules)")
    print(f"  Execution Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # Initialize Engines
    extractor = SpanishBillPDFExtractor(config)
    xml_parser = SAPXmlRdiParser(config)
    validator = SpanishRegulatoryValidator(config)
    reconciler = SapReconciler(config)
    visual_engine = VisualDiffEngine(config)
    reporter = ReportGenerator(output_dir)

    # Load SAP Backend Tables
    erdk_csv = os.path.join(sap_dir, "ERDK.csv")
    inv_h_csv = os.path.join(sap_dir, "DFKKINV_DOC_H.csv")
    inv_i_csv = os.path.join(sap_dir, "DFKKINV_DOC_I.csv")
    dfkkop_csv = os.path.join(sap_dir, "DFKKOP.csv")

    if all(os.path.exists(p) for p in [erdk_csv, inv_h_csv, inv_i_csv, dfkkop_csv]):
        print(f"[INFO] Loading SAP backend tables from: {sap_dir}")
        reconciler.load_sap_tables(erdk_csv, inv_h_csv, inv_i_csv, dfkkop_csv)
    else:
        print(f"[WARN] SAP table CSVs not found in {sap_dir}. Table reconciliation will be skipped or mock-matched.")

    # Discover Target Invoices
    target_files = [
        f for f in os.listdir(target_dir)
        if f.lower().endswith(".pdf") or f.lower().endswith(".xml") or f.lower().endswith(".rdi")
    ]

    if not target_files:
        print(f"[ERROR] No test files found in target directory: {target_dir}")
        sys.exit(1)

    print(f"[INFO] Discovered {len(target_files)} target invoice files for SIT/UAT validation.\n")

    records: list[ReconciliationRecord] = []
    math_results: list[MathValidationResult] = []
    tariff_counts: dict[str, dict[str, int]] = {}
    total_billed = Decimal("0.00")
    total_variance = Decimal("0.00")

    diff_img_dir = os.path.join(output_dir, "visual_diffs")
    os.makedirs(diff_img_dir, exist_ok=True)

    # Process each invoice
    for fname in target_files:
        t_path = os.path.join(target_dir, fname)
        print(f"--> Validating: {fname}")

        try:
            if fname.lower().endswith(".pdf"):
                bill = extractor.extract(t_path)
            else:
                bill = xml_parser.parse_file(t_path)

            # Regulatory Math Recalculation
            m_res = validator.validate_bill(bill)
            math_results.append(m_res)

            # SAP 3-Way Reconciliation
            r_rec = reconciler.reconcile_bill(bill, m_res)

            # Visual Layout Diff (if baseline exists and target is PDF)
            base_fname = fname.replace("_target.pdf", "_baseline.pdf")
            base_path = os.path.join(baseline_dir, base_fname)
            if not os.path.exists(base_path):
                # Search by invoice number
                for bf in os.listdir(baseline_dir):
                    if bill.invoice_number in bf and bf.lower().endswith(".pdf"):
                        base_path = os.path.join(baseline_dir, bf)
                        break

            if args.visual_diff and fname.lower().endswith(".pdf") and os.path.exists(base_path):
                v_diffs = visual_engine.compare_invoices(
                    base_path, t_path, diff_img_dir, invoice_number=bill.invoice_number
                )
                shifts = [vd for vd in v_diffs if vd.has_layout_shift]
                if shifts:
                    r_rec.visual_regression_passed = False
                    r_rec.defect_details.append(f"[LAYOUT] Layout shift detected (max: {shifts[0].max_shift_pt:.1f}pt)")

            records.append(r_rec)
            total_billed += r_rec.bill_total
            if r_rec.var_bill_vs_erdk:
                total_variance += abs(r_rec.var_bill_vs_erdk)

            # Tariff stats
            t_name = r_rec.tariff
            if t_name not in tariff_counts:
                tariff_counts[t_name] = {"total": 0, "pass": 0, "outsorted": 0, "fail": 0}
            tariff_counts[t_name]["total"] += 1
            if r_rec.overall_status == ReconcileStatus.PASS:
                tariff_counts[t_name]["pass"] += 1
            elif r_rec.overall_status == ReconcileStatus.OUTSORTED:
                tariff_counts[t_name]["outsorted"] += 1
            else:
                tariff_counts[t_name]["fail"] += 1

            status_str = f"[{r_rec.overall_status.value}]"
            print(f"    Status: {status_str:<12} Total: {r_rec.bill_total:>8.2f} €  CUPS: {r_rec.cups}  Tariff: {r_rec.tariff}")
            if r_rec.defect_details:
                for d in r_rec.defect_details:
                    print(f"      * {d}")

        except Exception as e:
            print(f"    [ERROR] Failed processing {fname}: {e}")

    # Build Summary
    passed_count = sum(1 for r in records if r.overall_status == ReconcileStatus.PASS)
    outsorted_count = sum(1 for r in records if r.overall_status == ReconcileStatus.OUTSORTED)
    failed_count = sum(1 for r in records if r.overall_status == ReconcileStatus.FAIL)
    total_count = len(records)
    pass_rate = (passed_count / total_count * 100) if total_count > 0 else 0.0

    summary = BatchReconciliationSummary(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total_invoices_tested=total_count,
        passed_count=passed_count,
        failed_count=failed_count,
        outsorted_count=outsorted_count,
        pass_rate_pct=pass_rate,
        total_billed_amount_eur=total_billed,
        total_financial_variance_eur=total_variance,
        tariff_breakdown=tariff_counts,
        records=records
    )

    # Generate Reports
    excel_path = reporter.generate_excel_report(summary, math_results)
    html_path = reporter.generate_html_report(summary, math_results)

    # Print Executive Summary Table
    print("\n" + "=" * 80)
    print("                      SIT/UAT BATCH VALIDATION SUMMARY")
    print("=" * 80)
    print(f"  Total Invoices Tested:     {total_count}")
    print(f"  Passed (Conforming):       {passed_count} ({pass_rate:.1f}%)")
    print(f"  Outsorted (Retenidas):     {outsorted_count}")
    print(f"  Failed (Critical Defect):  {failed_count}")
    print(f"  Total Billed Exposure:     {format_spanish_currency(total_billed)}")
    print(f"  Total Financial Variance:  {format_spanish_currency(total_variance)}")
    print("-" * 80)
    print("  Tariff Breakdown:")
    for t_key, t_data in tariff_counts.items():
        print(f"    - {t_key:<8}: Total: {t_data['total']:<3} | Pass: {t_data['pass']:<3} | Outsorted: {t_data['outsorted']:<3} | Fail: {t_data['fail']:<3}")
    print("-" * 80)
    print(f"  Excel Defect Report (JIRA): file:///{excel_path.replace(os.sep, '/')}")
    print(f"  HTML Sign-off Report:       file:///{html_path.replace(os.sep, '/')}")
    print("=" * 80 + "\n")

    if args.exit_code_on_fail and (outsorted_count > 0 or failed_count > 0):
        sys.exit(1)


if __name__ == "__main__":
    main()
