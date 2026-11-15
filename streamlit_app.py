"""
SAP IS-U & CI Automated Bill Print & Invoice Validation Suite (Spain Utility Edition)
Production-grade multi-tab Streamlit Dashboard.
"""

import os
import sys
import yaml
from decimal import Decimal
from datetime import datetime
import pandas as pd
from PIL import Image

import streamlit as st

# Configure page
st.set_page_config(
    page_title="SAP IS-U & CI Bill Validator | Spain Utility Edition",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Ensure project root is in sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

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


# ==============================================================================
# Custom CSS (SAP Fiori / Horizon Enterprise Theme)
# ==============================================================================

st.markdown("""
<style>
    /* SAP Enterprise Theme Colors */
    :root {
        --sap-primary: #1B365D;
        --sap-secondary: #0070BA;
        --sap-bg: #F4F6F9;
        --sap-card-bg: #FFFFFF;
        --sap-border: #E0E5EC;
        --sap-success: #107C41;
        --sap-warning: #D83B01;
        --sap-danger: #A80000;
    }
    
    .main {
        background-color: var(--sap-bg);
    }
    
    /* Top Header Bar */
    .sap-header {
        background: linear-gradient(135deg, #1B365D 0%, #2E5B88 100%);
        padding: 22px 28px;
        border-radius: 10px;
        color: white;
        margin-bottom: 24px;
        box-shadow: 0 4px 12px rgba(27, 54, 93, 0.12);
    }
    .sap-header h1 {
        font-size: 24px;
        font-weight: 700;
        margin: 0 0 6px 0;
        color: white !important;
    }
    .sap-header p {
        font-size: 13.5px;
        margin: 0;
        opacity: 0.9;
        color: #E2E8F0;
    }
    
    /* Metric Cards */
    .metric-card {
        background-color: var(--sap-card-bg);
        border: 1px solid var(--sap-border);
        border-radius: 10px;
        padding: 18px 20px;
        text-align: center;
        box-shadow: 0 2px 6px rgba(0,0,0,0.04);
        transition: transform 0.15s ease;
    }
    .metric-card:hover {
        transform: translateY(-2px);
    }
    .metric-title {
        font-size: 11.5px;
        font-weight: 600;
        text-transform: uppercase;
        color: #64748B;
        margin-bottom: 6px;
        letter-spacing: 0.5px;
    }
    .metric-val {
        font-size: 26px;
        font-weight: 700;
        color: #1B365D;
    }
    .metric-val.pass { color: #107C41; }
    .metric-val.outsort { color: #D83B01; }
    .metric-val.fail { color: #A80000; }
    
    /* Status Badges */
    .status-badge {
        display: inline-block;
        padding: 3px 10px;
        border-radius: 14px;
        font-size: 11px;
        font-weight: 700;
        text-transform: uppercase;
    }
    .badge-pass { background-color: #D1E7DD; color: #0F5132; border: 1px solid #BADBCC; }
    .badge-outsort { background-color: #FFF3CD; color: #664D03; border: 1px solid #FFECB5; }
    .badge-fail { background-color: #F8D7DA; color: #842029; border: 1px solid #F5C2C7; }
    .badge-tariff { background-color: #E2E8F0; color: #1E293B; font-weight: 600; }
    
    /* Callout Alert Boxes */
    .alert-outsort {
        background-color: #FFF8E6;
        border-left: 5px solid #D83B01;
        padding: 14px 18px;
        border-radius: 6px;
        margin-bottom: 16px;
        color: #7A2E00;
        font-size: 13px;
    }
    .alert-pass {
        background-color: #F0FDF4;
        border-left: 5px solid #107C41;
        padding: 14px 18px;
        border-radius: 6px;
        margin-bottom: 16px;
        color: #14532D;
        font-size: 13px;
    }
    
    /* Tab styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        border-bottom: 2px solid #E2E8F0;
    }
    .stTabs [data-baseweb="tab"] {
        font-weight: 600;
        font-size: 13.5px;
        padding: 10px 18px;
    }
</style>
""", unsafe_allow_html=True)


# ==============================================================================
# State Initialization & Caching
# ==============================================================================

@st.cache_resource
def get_config():
    cfg_path = os.path.join(BASE_DIR, "config", "spanish_tariff_rules.yaml")
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}


config = get_config()

# Ensure directories exist
baseline_dir = os.path.join(BASE_DIR, "data", "baseline")
target_dir = os.path.join(BASE_DIR, "data", "target")
sap_dir = os.path.join(BASE_DIR, "data", "sap_tables")
reports_dir = os.path.join(BASE_DIR, "reports")
os.makedirs(baseline_dir, exist_ok=True)
os.makedirs(target_dir, exist_ok=True)
os.makedirs(sap_dir, exist_ok=True)
os.makedirs(reports_dir, exist_ok=True)

# Auto-seed synthetic dataset on first cloud/container boot if empty
if not os.path.exists(target_dir) or not os.listdir(target_dir):
    try:
        gen = SampleDataGenerator(BASE_DIR)
        gen.generate_all()
    except Exception:
        pass


@st.cache_data
def run_batch_validation(amount_tol: float, outsort_thresh: float, layout_tol: float):
    """Runs batch extraction, math recalculation, 3-way reconciliation, and visual diff."""
    run_cfg = dict(config)
    run_cfg.setdefault('tolerances', {})['amount_tolerance_eur'] = amount_tol
    run_cfg['tolerances']['outsorting_threshold_eur'] = outsort_thresh
    run_cfg['tolerances']['layout_shift_tolerance_pt'] = layout_tol

    extractor = SpanishBillPDFExtractor(run_cfg)
    xml_parser = SAPXmlRdiParser(run_cfg)
    validator = SpanishRegulatoryValidator(run_cfg)
    reconciler = SapReconciler(run_cfg)
    visual_engine = VisualDiffEngine(run_cfg)
    reporter = ReportGenerator(reports_dir)

    # Load SAP Tables
    erdk_path = os.path.join(sap_dir, "ERDK.csv")
    inv_h_path = os.path.join(sap_dir, "DFKKINV_DOC_H.csv")
    inv_i_path = os.path.join(sap_dir, "DFKKINV_DOC_I.csv")
    dfkkop_path = os.path.join(sap_dir, "DFKKOP.csv")

    if all(os.path.exists(p) for p in [erdk_path, inv_h_path, inv_i_path, dfkkop_path]):
        reconciler.load_sap_tables(erdk_path, inv_h_path, inv_i_path, dfkkop_path)

    # Find target files
    files = [f for f in os.listdir(target_dir) if f.lower().endswith((".pdf", ".xml", ".rdi"))]
    files.sort()

    records = []
    math_results = []
    extracted_bills = {}
    visual_results = {}
    tariff_counts = {}
    total_billed = Decimal("0.00")
    total_variance = Decimal("0.00")

    diff_img_dir = os.path.join(reports_dir, "visual_diffs")
    os.makedirs(diff_img_dir, exist_ok=True)

    for fname in files:
        t_path = os.path.join(target_dir, fname)
        try:
            if fname.lower().endswith(".pdf"):
                bill = extractor.extract(t_path)
            else:
                bill = xml_parser.parse_file(t_path)

            extracted_bills[bill.invoice_number] = bill

            # Mathematical Validation
            m_res = validator.validate_bill(bill)
            math_results.append(m_res)

            # 3-Way Reconciliation
            r_rec = reconciler.reconcile_bill(bill, m_res)

            # Visual Diff
            base_fname = fname.replace("_target.pdf", "_baseline.pdf")
            base_path = os.path.join(baseline_dir, base_fname)
            if not os.path.exists(base_path):
                for bf in os.listdir(baseline_dir):
                    if bill.invoice_number in bf and bf.lower().endswith(".pdf"):
                        base_path = os.path.join(baseline_dir, bf)
                        break

            if fname.lower().endswith(".pdf") and os.path.exists(base_path):
                v_diffs = visual_engine.compare_invoices(
                    base_path, t_path, diff_img_dir, invoice_number=bill.invoice_number
                )
                visual_results[bill.invoice_number] = v_diffs
                shifts = [vd for vd in v_diffs if vd.has_layout_shift]
                if shifts:
                    r_rec.visual_regression_passed = False
                    r_rec.defect_details.append(f"[LAYOUT] Layout shift detectado (max: {shifts[0].max_shift_pt:.1f}pt)")

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

        except Exception as e:
            st.error(f"Error processing {fname}: {e}")

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

    excel_file = reporter.generate_excel_report(summary, math_results)
    html_file = reporter.generate_html_report(summary, math_results)

    return summary, math_results, extracted_bills, visual_results, excel_file, html_file


# ==============================================================================
# Sidebar Controls
# ==============================================================================

with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/5/59/SAP_2011_logo.svg", width=110)
    st.markdown("### **Configuración del Entorno**")

    st.markdown("**Regulaciones Españolas (CNMC / BOE)**")
    st.caption("Circular 3/2020 CNMC | RD 148/2021 | Ley 38/1992 IEE | Ley 37/1992 IVA")

    st.markdown("---")
    st.markdown("#### **Tolerancias de Facturación**")
    amount_tol = st.number_input(
        "Tolerancia de Redondeo (€)",
        min_value=0.00,
        max_value=0.10,
        value=0.01,
        step=0.01,
        help="Permite hasta ±0.01 € de discrepancia por redondeo bancario."
    )
    outsort_thresh = st.number_input(
        "Umbral de Retención IS-U / EASIB (€)",
        min_value=0.01,
        max_value=1.00,
        value=0.05,
        step=0.01,
        help="Discrepancias superiores a este importe marcan la factura como OUTSORTED."
    )
    layout_tol = st.slider(
        "Tolerancia Desplazamiento Visual (pt)",
        min_value=0.5,
        max_value=10.0,
        value=2.5,
        step=0.5,
        help="Desplazamientos superiores a este umbral se marcan como error de layout."
    )

    st.markdown("---")
    if st.button("🔄 Ejecutar Validación Batch", type="primary", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    if st.button("🛠️ Regenerar Datos de Prueba Sintéticos", use_container_width=True):
        gen = SampleDataGenerator(BASE_DIR)
        gen.generate_all()
        st.cache_data.clear()
        st.success("Dataset sintético regenerado con éxito.")
        st.rerun()

    st.markdown("---")
    st.caption("SAP S/4HANA Utilities | CI & FI-CA Validation | v1.0.0")


# ==============================================================================
# Header Banner
# ==============================================================================

st.markdown("""
<div class="sap-header">
    <h1>⚡ SAP IS-U & CI Automated Bill Print & Invoice Validation Suite</h1>
    <p>Spain Utility Edition — 3-Way Reconciliation (Bill PDF/XML ⟷ Regulatory Math ⟷ SAP Backend: ERDK, DFKKINV_DOC_H/I, DFKKOP)</p>
</div>
""", unsafe_allow_html=True)


# Run Validation
summary, math_results, extracted_bills, visual_results, excel_path, html_path = run_batch_validation(
    amount_tol, outsort_thresh, layout_tol
)

# Convert math results to dictionary by invoice_number
math_map = {m.invoice_number: m for m in math_results}


# ==============================================================================
# Main Multi-Tab Interface
# ==============================================================================

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📊 Resumen Ejecutivo SIT/UAT",
    "🔍 Visual & Layout Diff (PyMuPDF)",
    "⚖️ Auditoría Matemática (CNMC / BOE)",
    "🗄️ Reconciliación 3-Way (Tablas SAP)",
    "📲 Veri*factu QR & Spool XML",
    "📥 Informes y Defectos JIRA"
])


# ------------------------------------------------------------------------------
# TAB 1: Resumen Ejecutivo SIT/UAT
# ------------------------------------------------------------------------------
with tab1:
    st.markdown("### **Panel de Control de Pruebas de Regresión (SIT / UAT)**")
    st.markdown("Indicadores clave de rendimiento para la certificación de formularios de facturación antes del pase a producción.")

    # KPI Metrics Row
    k1, k2, k3, k4, k5 = st.columns(5)
    with k1:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-title">Facturas Verificadas</div>
            <div class="metric-val">{summary.total_invoices_tested}</div>
        </div>
        """, unsafe_allow_html=True)
    with k2:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-title">Tasa de Aprobación (Pass %)</div>
            <div class="metric-val {'pass' if summary.pass_rate_pct >= 90 else 'fail'}">{summary.pass_rate_pct:.1f}%</div>
        </div>
        """, unsafe_allow_html=True)
    with k3:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-title">Facturas Retenidas (EASIB)</div>
            <div class="metric-val {'outsort' if summary.outsorted_count > 0 else 'pass'}">{summary.outsorted_count}</div>
        </div>
        """, unsafe_allow_html=True)
    with k4:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-title">Defectos Críticos</div>
            <div class="metric-val {'fail' if summary.failed_count > 0 else 'pass'}">{summary.failed_count}</div>
        </div>
        """, unsafe_allow_html=True)
    with k5:
        st.markdown(f"""
        <div class="metric-card">
            <div class="metric-title">Impacto Financiero (€)</div>
            <div class="metric-val">{format_spanish_currency(summary.total_financial_variance_eur)}</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Tariff Breakdown & Quick Chart
    c_left, c_right = st.columns([1, 2])
    with c_left:
        st.markdown("#### **Desglose por Tarifa de Acceso**")
        tariff_rows = []
        for t_name, t_data in summary.tariff_breakdown.items():
            tot = t_data["total"]
            pas = t_data["pass"]
            out = t_data["outsorted"]
            fai = t_data["fail"]
            rate = f"{(pas / tot * 100):.1f}%" if tot > 0 else "0%"
            tariff_rows.append({
                "Tarifa": t_name,
                "Total": tot,
                "Aprobadas": pas,
                "Retenidas": out,
                "Fallidas": fai,
                "% Aprobación": rate
            })
        st.dataframe(pd.DataFrame(tariff_rows), hide_index=True, use_container_width=True)

    with c_right:
        st.markdown("#### **Distribución de Estado de Facturas**")
        chart_data = pd.DataFrame({
            "Estado": ["Aprobadas (PASS)", "Retenidas (OUTSORTED)", "Fallidas (FAIL)"],
            "Cantidad": [summary.passed_count, summary.outsorted_count, summary.failed_count]
        })
        st.bar_chart(chart_data, x="Estado", y="Cantidad", color="Estado", use_container_width=True)

    st.markdown("---")
    st.markdown("#### **Registro General de Facturas Verificadas**")

    # Filter Bar
    f_col1, f_col2, f_col3 = st.columns([2, 2, 4])
    with f_col1:
        status_filter = st.selectbox("Filtrar por Estado:", ["TODOS", "PASS", "OUTSORTED", "FAIL"])
    with f_col2:
        tariff_filter = st.selectbox("Filtrar por Tarifa:", ["TODAS"] + list(summary.tariff_breakdown.keys()))

    # Build filtered table
    table_data = []
    for r in summary.records:
        if status_filter != "TODOS" and r.overall_status.value != status_filter:
            continue
        if tariff_filter != "TODAS" and r.tariff != tariff_filter:
            continue

        table_data.append({
            "Factura Nº": r.invoice_number,
            "CUPS": r.cups,
            "Cuenta Contrato": r.contract_account or "N/A",
            "Tarifa": r.tariff,
            "Total Factura (€)": float(r.bill_total),
            "ERDK Total (€)": float(r.erdk_total) if r.erdk_total else None,
            "DFKKOP Open (€)": float(r.dfkkop_total) if r.dfkkop_total else None,
            "Desviación (€)": float(r.var_bill_vs_erdk) if r.var_bill_vs_erdk else 0.0,
            "Estado": r.overall_status.value,
            "Retenida": "SÍ" if r.is_outsorted else "NO",
            "Defectos": "; ".join(r.defect_details) if r.defect_details else "Ninguno"
        })

    if table_data:
        df_display = pd.DataFrame(table_data)
        st.dataframe(
            df_display,
            column_config={
                "Total Factura (€)": st.column_config.NumberColumn(format="%.2f €"),
                "ERDK Total (€)": st.column_config.NumberColumn(format="%.2f €"),
                "DFKKOP Open (€)": st.column_config.NumberColumn(format="%.2f €"),
                "Desviación (€)": st.column_config.NumberColumn(format="%+.2f €"),
            },
            hide_index=True,
            use_container_width=True
        )
    else:
        st.info("No se encontraron facturas con los filtros seleccionados.")


# ------------------------------------------------------------------------------
# TAB 2: Visual & Layout Diff (PyMuPDF)
# ------------------------------------------------------------------------------
with tab2:
    st.markdown("### **Comparador Visual de Diseños y Regresiones de Maquetación**")
    st.markdown(
        "Inspecciona desplazamientos de coordenadas de maquetación (Layout Shifts) en formularios ADS / SmartForms, "
        "con enmascaramiento dinámico de Spool IDs, horas de impresión y contadores de página."
    )

    # Select Bill
    bill_options = list(visual_results.keys())
    if not bill_options:
        bill_options = [r.invoice_number for r in summary.records]

    selected_inv = st.selectbox(
        "Seleccione Factura para Comparación Visual:",
        options=bill_options,
        index=bill_options.index("10000007") if "10000007" in bill_options else 0
    )

    col_ctrl1, col_ctrl2 = st.columns(2)
    with col_ctrl1:
        mask_toggle = st.checkbox("Activar Enmascaramiento Dinámico de Spool y Timestamps", value=True)
    with col_ctrl2:
        highlight_shifts = st.checkbox("Resaltar Desplazamientos de Maquetación en Azul", value=True)

    v_pages = visual_results.get(selected_inv, [])
    if v_pages:
        for vp in v_pages:
            st.markdown(f"#### **Página {vp.page_number} — Análisis de Regresión**")

            if vp.has_layout_shift:
                st.markdown(f"""
                <div class="alert-outsort">
                    <strong>⚠️ ALERTA DE MAQUETACIÓN:</strong> Se detectó un desplazamiento máximo de <strong>{vp.max_shift_pt:.1f} pt</strong> 
                    (Umbral configurado: {layout_tol:.1f} pt). Revise el espaciado vertical de tablas o tamaños de fuente en el SmartForm/ADS.
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div class="alert-pass">
                    <strong>✅ MAQUETACIÓN CONFORME:</strong> Sin desplazamientos indebidos en las posiciones de texto o importes.
                </div>
                """, unsafe_allow_html=True)

            # 3 Columns Preview: Baseline, Target, Overlay
            v_col1, v_col2, v_col3 = st.columns(3)

            with v_col1:
                st.markdown("**1. Factura Baseline (Referencia)**")
                if vp.image_baseline_path and os.path.exists(vp.image_baseline_path):
                    st.image(vp.image_baseline_path, use_container_width=True)
                else:
                    st.info("Imagen baseline no disponible.")

            with v_col2:
                st.markdown("**2. Factura Target (Candidata)**")
                if vp.image_target_path and os.path.exists(vp.image_target_path):
                    st.image(vp.image_target_path, use_container_width=True)
                else:
                    st.info("Imagen target no disponible.")

            with v_col3:
                st.markdown("**3. Máscara de Regresión & Bounding Boxes**")
                if vp.image_overlay_path and os.path.exists(vp.image_overlay_path):
                    st.image(vp.image_overlay_path, use_container_width=True)
                    st.caption("🔴 Rojo: Discrepancia de Texto/Importe | 🔵 Azul: Desplazamiento Layout | 🟠 Ámbar: Enmascarado Dinámico")
                else:
                    st.info("Imagen overlay no disponible.")

            # Layout Shift Token Table
            shifts_detected = [d for d in vp.field_diffs if d.status.value in ("LAYOUT_SHIFT", "DISCREPANCY")]
            if shifts_detected:
                st.markdown("##### **Detalle de Desviaciones de Texto y Coordenadas**")
                diff_table = []
                for sd in shifts_detected:
                    diff_table.append({
                        "Elemento": sd.field_name,
                        "Valor Baseline": sd.baseline_value,
                        "Valor Target": sd.target_value,
                        "Diferencia": sd.difference,
                        "Tipo": sd.status.value,
                        "Coordenadas Bounding Box [x0, y0, x1, y1]": str([round(c, 1) for c in (sd.bounding_box or [])])
                    })
                st.dataframe(pd.DataFrame(diff_table), hide_index=True, use_container_width=True)

    else:
        st.warning("No hay datos de comparación visual para esta factura (es posible que sea un archivo XML o no tenga PDF de baseline).")


# ------------------------------------------------------------------------------
# TAB 3: Auditoría Matemática (CNMC / BOE)
# ------------------------------------------------------------------------------
with tab3:
    st.markdown("### **Auditoría Matemática y Regulatoria (CNMC / BOE)**")
    st.markdown(
        "Comprobación independiente de fórmulas de peajes, cargos del sistema, impuesto eléctrico (IEE), "
        "canon de bono social, alquiler de equipos de medida e IVA."
    )

    math_inv_options = list(math_map.keys())
    selected_math_inv = st.selectbox(
        "Seleccione Factura para Auditoría Matemática:",
        options=math_inv_options,
        index=0
    )

    m_record = math_map.get(selected_math_inv)
    bill_extract = extracted_bills.get(selected_math_inv)

    if m_record and bill_extract:
        # Outsorting Status Banner
        if m_record.is_outsorted:
            st.markdown(f"""
            <div class="alert-outsort">
                <strong>🚨 FACTURA RETENIDA EN FACTURACIÓN IS-U (EASIB / EA05):</strong><br>
                La factura no supera la validación matemática regulatoria. Motivos de retención:
                <ul>
                    {"".join(f"<li>{r}</li>" for r in m_record.outsort_reasons)}
                </ul>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div class="alert-pass">
                <strong>✅ FACTURA LIBERADA PARA FACTURACIÓN:</strong> Todas las fórmulas aritméticas e impuestos cumplen 
                rigurosamente con la normativa española de la CNMC y el BOE.
            </div>
            """, unsafe_allow_html=True)

        # Header summary
        h1, h2, h3, h4 = st.columns(4)
        h1.metric("CUPS", bill_extract.cups)
        h2.metric("Tarifa de Acceso", bill_extract.tariff.value)
        h3.metric("Días Facturados", f"{bill_extract.billing_days} días")
        h4.metric("Total Factura", format_spanish_currency(bill_extract.total_amount))

        st.markdown("#### **Desglose de Recalculación Aritmética Paso a Paso**")

        comp_data = []
        for c in m_record.components:
            comp_data.append({
                "Concepto Facturado": c.component,
                "Fórmula / Desglose Aritmético": c.formula,
                "Importe Extraído (€)": float(c.extracted_value),
                "Importe Recalculado (€)": float(c.recalculated_value),
                "Desviación (€)": float(c.variance),
                "Resultado": "CONFORME" if c.tolerance_passed else "DISCREPANCIA",
                "Referencia Legal (BOE/CNMC)": c.regulation_reference
            })

        df_comps = pd.DataFrame(comp_data)
        st.dataframe(
            df_comps,
            column_config={
                "Importe Extraído (€)": st.column_config.NumberColumn(format="%.2f €"),
                "Importe Recalculado (€)": st.column_config.NumberColumn(format="%.2f €"),
                "Desviación (€)": st.column_config.NumberColumn(format="%+.2f €"),
            },
            hide_index=True,
            use_container_width=True
        )

        st.markdown("---")
        st.markdown("#### **Períodos de Potencia y Energía Detectados**")
        p_col, e_col = st.columns(2)

        with p_col:
            st.markdown("**Términos de Potencia Contratada**")
            p_rows = []
            for p in bill_extract.power_periods:
                p_rows.append({
                    "Periodo": p.period,
                    "Potencia (kW)": float(p.contracted_power_kw),
                    "Días": p.days,
                    "Precio (€/kW/día)": float(p.unit_price_eur_kw_day),
                    "Total (€)": float(p.total_eur)
                })
            if p_rows:
                st.dataframe(pd.DataFrame(p_rows), hide_index=True, use_container_width=True)
            else:
                st.info("Sin periodos detallados de potencia.")

        with e_col:
            st.markdown("**Términos de Energía Activa Consumida**")
            e_rows = []
            for e in bill_extract.energy_periods:
                e_rows.append({
                    "Periodo": e.period,
                    "Consumo (kWh)": float(e.consumption_kwh),
                    "Precio (€/kWh)": float(e.unit_price_eur_kwh),
                    "Total (€)": float(e.total_eur)
                })
            if e_rows:
                st.dataframe(pd.DataFrame(e_rows), hide_index=True, use_container_width=True)
            else:
                st.info("Sin periodos detallados de energía.")


# ------------------------------------------------------------------------------
# TAB 4: Reconciliación 3-Way (Tablas SAP)
# ------------------------------------------------------------------------------
with tab4:
    st.markdown("### **Reconciliación 3-Way contra Tablas SAP IS-U & CI**")
    st.markdown(
        "Cruza automáticamente los datos extraídos de la factura física (PDF/XML) contra las tablas "
        "`ERDK` (Cabecera Documento Impresión), `DFKKINV_DOC_H` / `DFKKINV_DOC_I` (Facturación CI), y `DFKKOP` (Partidas Abiertas FI-CA)."
    )

    recon_inv_options = [r.invoice_number for r in summary.records]
    selected_recon_inv = st.selectbox(
        "Seleccione Factura para Auditoría SAP 3-Way:",
        options=recon_inv_options,
        index=recon_inv_options.index("10000008") if "10000008" in recon_inv_options else 0
    )

    rec_item = next((r for r in summary.records if r.invoice_number == selected_recon_inv), None)

    if rec_item:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Importe Factura PDF", format_spanish_currency(rec_item.bill_total))
        c2.metric("SAP ERDK-TOTAL_AMNT", format_spanish_currency(rec_item.erdk_total) if rec_item.erdk_total else "N/A")
        c3.metric("SAP CI Doc H", format_spanish_currency(rec_item.inv_doc_h_total) if rec_item.inv_doc_h_total else "N/A")
        c4.metric("SAP DFKKOP (Partida Abierta)", format_spanish_currency(rec_item.dfkkop_total) if rec_item.dfkkop_total else "N/A")

        if rec_item.defect_details:
            st.markdown(f"""
            <div class="alert-outsort">
                <strong>Descuadres Identificados en Backend SAP:</strong>
                <ul>
                    {"".join(f"<li>{d}</li>" for d in rec_item.defect_details)}
                </ul>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div class="alert-pass">
                <strong>✅ CONCILIACIÓN PERFECTA:</strong> Coincidencia exacta de cabecera, líneas e importes abiertos en FI-CA.
            </div>
            """, unsafe_allow_html=True)

        st.markdown("#### **Conciliación de Líneas de Facturación CI (DFKKINV_DOC_I)**")
        if rec_item.item_reconciliations:
            ci_table = []
            for itm in rec_item.item_reconciliations:
                ci_table.append({
                    "Concepto": itm["concept"],
                    "Sub-Aplicación CI": itm["sub_ap_id"],
                    "Importe Factura PDF (€)": float(itm["bill_amount"]),
                    "Importe SAP CI (€)": float(itm["sap_amount"]),
                    "Desviación (€)": float(itm["variance"]),
                    "Estado": "CONFORME" if itm["matched"] else "DISPAR"
                })
            st.dataframe(pd.DataFrame(ci_table), hide_index=True, use_container_width=True)
        else:
            st.info("Sin desglose de líneas individuales de facturación CI.")


# ------------------------------------------------------------------------------
# TAB 5: Veri*factu QR & Spool XML
# ------------------------------------------------------------------------------
with tab5:
    st.markdown("### **Inspección de Códigos QR Veri*factu / FacturaE & Spool XML/RDI**")
    st.markdown(
        "Decodifica y valida el código QR obligatorio para el sistema Veri*factu de la Agencia Tributaria Española, "
        "y permite explorar la estructura nativa de los spools generados por SAP Print Workbench (`EFRM`)."
    )

    qr_inv_options = [r.invoice_number for r in summary.records]
    selected_qr_inv = st.selectbox(
        "Seleccione Factura para Inspección de QR y Spool:",
        options=qr_inv_options,
        index=0
    )

    bill_data = extracted_bills.get(selected_qr_inv)

    if bill_data:
        qr_col, xml_col = st.columns([1, 2])

        with qr_col:
            st.markdown("#### **Código QR Veri*factu**")
            # Generate QR image for display
            qr_content = bill_data.qr_content or f"https://verifactu.agenciatributaria.gob.es/valida?nif={bill_data.fiscal_id}&num={bill_data.invoice_number}&fecha={bill_data.issue_date}&total={bill_data.total_amount}"
            
            import qrcode
            import io
            qr_img = qrcode.make(qr_content)
            buf = io.BytesIO()
            qr_img.save(buf, format="PNG")
            st.image(buf.getvalue(), caption="Veri*factu / FacturaE QR Oficial", width=220)

            st.markdown("**Contenido Decodificado:**")
            st.code(qr_content, language="text")

            # Validate parameters in QR
            st.markdown("**Parámetros Fiscales Detectados:**")
            st.markdown(f"- **NIF/CIF:** `{bill_data.fiscal_id or 'No informado'}`")
            st.markdown(f"- **Factura:** `{bill_data.invoice_number}`")
            st.markdown(f"- **Fecha:** `{bill_data.issue_date or 'No informada'}`")
            st.markdown(f"- **Importe Total:** `{format_spanish_currency(bill_data.total_amount)}`")

        with xml_col:
            st.markdown("#### **Estructura del Spool SAP Print Workbench (XML / RDI)**")
            xml_path = os.path.join(target_dir, f"{selected_qr_inv}_spool.xml")
            if os.path.exists(xml_path):
                with open(xml_path, "r", encoding="utf-8") as xf:
                    xml_text = xf.read()
                st.code(xml_text, language="xml")
            else:
                st.info("Archivo de spool XML no encontrado para esta factura.")


# ------------------------------------------------------------------------------
# TAB 6: Informes y Defectos JIRA
# ------------------------------------------------------------------------------
with tab6:
    st.markdown("### **Centro de Informes de Auditoría y Exportación de Defectos (JIRA / QMetry)**")
    st.markdown(
        "Descarga el dossier completo de certificación de pruebas en formato Excel (preformateado con fórmulas "
        "y estilos para importación directa en JIRA / QMetry) o el informe ejecutivo en HTML autoportante."
    )

    down_col1, down_col2 = st.columns(2)

    with down_col1:
        st.markdown("#### 📑 **Dossier de Auditoría en Excel**")
        st.markdown("Incluye 4 pestañas: Resumen Ejecutivo, Matriz 3-Way, Registro de Defectos JIRA y Auditoría Matemática.")
        if os.path.exists(excel_path):
            with open(excel_path, "rb") as ef:
                st.download_button(
                    label="📥 Descargar Dossier Excel (.xlsx)",
                    data=ef.read(),
                    file_name=os.path.basename(excel_path),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary",
                    use_container_width=True
                )

    with down_col2:
        st.markdown("#### 🌐 **Informe Ejecutivo de Certificación HTML**")
        st.markdown("Informe interactivo, autónomo y listo para imprimir, con formato de certificación formal para SIT/UAT.")
        if os.path.exists(html_path):
            with open(html_path, "rb") as hf:
                st.download_button(
                    label="📥 Descargar Informe HTML (.html)",
                    data=hf.read(),
                    file_name=os.path.basename(html_path),
                    mime="text/html",
                    use_container_width=True
                )

    st.markdown("---")
    st.markdown("#### **Previsualización de Defectos para JIRA / QMetry**")

    jira_defects = []
    bug_idx = 1
    for r in summary.records:
        if r.overall_status != ReconcileStatus.PASS or r.defect_details:
            for d in r.defect_details:
                jira_defects.append({
                    "Clave": f"ISU-DEF-{bug_idx:03d}",
                    "Severidad": "Crítica (Outsort)" if r.is_outsorted else "Mayor",
                    "Componente": "SAP Print Workbench / FI-CA",
                    "Factura": r.invoice_number,
                    "Tarifa": r.tariff,
                    "Resumen Defecto": d,
                    "Retención EASIB": "SÍ" if r.is_outsorted else "NO"
                })
                bug_idx += 1

    if jira_defects:
        st.dataframe(pd.DataFrame(jira_defects), hide_index=True, use_container_width=True)
    else:
        st.success("No se encontraron defectos abiertos. Todos los formularios cumplen las especificaciones de diseño y cálculo.")
