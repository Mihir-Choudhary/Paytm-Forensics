"""Court-ready HTML report generator (FR-G7, EV-2/EV-3/EV-4).

Self-contained HTML (no external assets) built from the case DB + case_meta.json +
manifest.json. Embeds case metadata, input-manifest hash summary, per-domain sections
with source attribution, and an integrity note. After writing, the report's own SHA-256
is computed and written to a .sha256 sidecar. PDF is best-effort via WeasyPrint.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
from datetime import datetime, timezone

from ..gui.datasource import DataSource, DISPLAY_COLUMNS, DOMAIN_LABELS

# domains rendered as full tables, in report order
REPORT_DOMAINS = ["entity", "transaction", "message", "location", "consent",
                  "job", "notification", "search", "diagnostic", "carved",
                  "appstate", "cookie", "webstorage", "webcache", "capability",
                  "crash", "encrypted", "config", "pref", "timeline"]


def _esc(v) -> str:
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False)
    return html.escape("" if v is None else str(v))


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _bar_svg(pairs, color="#2f81f7", unit="") -> str:
    """Tiny inline SVG horizontal bar chart from [(label, value)] (no JS/deps)."""
    pairs = [(str(l), float(v or 0)) for l, v in pairs if v]
    if not pairs:
        return "<p><em>(no data)</em></p>"
    mx = max(v for _l, v in pairs) or 1
    rowh, w = 26, 470
    rows = []
    for i, (lab, v) in enumerate(pairs):
        bw = int((w - 200) * v / mx)
        y = i * rowh
        rows.append(
            f'<text x="0" y="{y+15}" font-size="11" fill="#8b949e">{_esc(lab[:22])}</text>'
            f'<rect x="150" y="{y+3}" width="{bw}" height="16" rx="3" fill="{color}"/>'
            f'<text x="{150+bw+6}" y="{y+15}" font-size="11" fill="#444">{unit}{v:g}</text>')
    h = len(pairs) * rowh + 6
    return f'<svg width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg">{"".join(rows)}</svg>'


def _charts(ds: DataSource) -> str:
    from collections import defaultdict
    txns = ds.load("transaction")
    settled = [t for t in txns if t.get("settled")]
    cat = defaultdict(float)
    for t in settled:
        if t.get("direction") == "debit":
            cat[t.get("category_label") or "uncategorised"] += t.get("amount") or 0
    cat_pairs = sorted(cat.items(), key=lambda kv: -kv[1])[:8]
    bymon = defaultdict(int)
    for t in txns:
        utc = (t.get("timestamp") or {}).get("utc_iso")
        if utc:
            bymon[utc[:7]] += 1
    mon_pairs = sorted(bymon.items())[-12:]
    credit = sum(t.get("amount") or 0 for t in settled if t.get("direction") == "credit")
    debit = sum(t.get("amount") or 0 for t in settled if t.get("direction") == "debit")
    return (
        "<h2>Analytics</h2>"
        "<table><tr><td style='border:none;vertical-align:top'>"
        "<b>Money in vs out (settled)</b><br>"
        + _bar_svg([("Received", credit), ("Paid", debit)], "#3fb950", "₹")
        + "</td><td style='border:none;vertical-align:top'>"
        "<b>Spend by category</b><br>" + _bar_svg(cat_pairs, "#f0883e", "₹")
        + "</td></tr><tr><td colspan='2' style='border:none'>"
        "<b>Transactions per month</b><br>" + _bar_svg(mon_pairs, "#2f81f7")
        + "</td></tr></table>")


def _table(ds: DataSource, domain: str, limit: int | None = None) -> str:
    rows = ds.load(domain)
    if not rows:
        return "<p><em>(no records)</em></p>"
    cols = ds.columns(domain)
    shown = rows[:limit] if limit else rows
    out = ["<table><thead><tr>"]
    out += [f"<th>{_esc(c)}</th>" for c in cols]
    out.append("<th>source</th><th>origin</th></tr></thead><tbody>")
    for r in shown:
        out.append("<tr>")
        for c in cols:
            out.append(f"<td>{_esc(ds.cell(r, c))}</td>")
        p = r.get("provenance", {})
        out.append(f"<td class='prov'>{_esc(p.get('source_file'))}"
                   f"{(' :: ' + _esc(p.get('source_table'))) if p.get('source_table') else ''}</td>")
        out.append(f"<td class='origin-{_esc(p.get('origin'))}'>{_esc(p.get('origin'))}</td>")
        out.append("</tr>")
    out.append("</tbody></table>")
    if limit and len(rows) > limit:
        out.append(f"<p><em>… {len(rows) - limit} more rows (see JSON/CSV export)</em></p>")
    return "".join(out)


def build_html(case_dir: str, *, table_limit: int | None = None) -> str:
    meta = _load_json(os.path.join(case_dir, "case_meta.json"))
    manifest = _load_json(os.path.join(case_dir, "manifest.json"))
    ds = DataSource(os.path.join(case_dir, "case.db"))
    counts = ds.domains()

    gen_ts = datetime.now(timezone.utc).isoformat()
    files = manifest.get("files", [])
    file_count = manifest.get("file_count", len(files))

    parts = [f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>PaytmForensics Report — {_esc(meta.get('case_id'))}</title>
<style>
 body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1a1a1a}}
 h1{{border-bottom:3px solid #00b9f5}} h2{{margin-top:28px;border-bottom:1px solid #ccc}}
 table{{border-collapse:collapse;width:100%;font-size:12px;margin:8px 0}}
 th,td{{border:1px solid #ccc;padding:4px 6px;text-align:left;vertical-align:top}}
 th{{background:#f0f8ff}} .prov{{color:#555;font-family:monospace;font-size:11px}}
 .origin-carved{{color:#b30000;font-weight:bold}} .origin-live{{color:#060}}
 .meta td{{border:none;padding:2px 8px}} .warn{{background:#fff7e6;padding:8px;border:1px solid #ffcc66}}
 .summary span{{display:inline-block;background:#eef;padding:3px 8px;margin:2px;border-radius:4px}}
</style></head><body>
<h1>PaytmForensics — Forensic Analysis Report</h1>
<table class="meta">
 <tr><td><b>Case ID</b></td><td>{_esc(meta.get('case_id'))}</td>
     <td><b>Examiner</b></td><td>{_esc(meta.get('examiner'))}</td></tr>
 <tr><td><b>Evidence #</b></td><td>{_esc(meta.get('evidence_number'))}</td>
     <td><b>Tool version</b></td><td>{_esc(meta.get('tool_version'))}</td></tr>
 <tr><td><b>Extraction</b></td><td colspan="3">{_esc(meta.get('extraction_root'))}</td></tr>
 <tr><td><b>Ingested (UTC)</b></td><td>{_esc(meta.get('created_utc'))}</td>
     <td><b>Report (UTC)</b></td><td>{_esc(gen_ts)}</td></tr>
 <tr><td><b>Input files hashed</b></td><td colspan="3">{file_count}</td></tr>
 <tr><td><b>Notes</b></td><td colspan="3">{_esc(meta.get('notes'))}</td></tr>
</table>
<div class="warn"><b>Integrity & method:</b> Source opened read-only (immutable); every record
is attributed to its source file/table with the file's SHA-256 recorded in manifest.json.
Records labelled <span class="origin-carved">carved</span> were recovered from free/slack
space and carry a confidence &lt; 1.0. Encrypted, hardware-keystore-bound artifacts are
catalogued, not decrypted.</div>
<h2>Summary</h2><div class="summary">"""]
    for dom in REPORT_DOMAINS:
        if dom in counts:
            parts.append(f"<span>{_esc(DOMAIN_LABELS.get(dom, dom))}: <b>{counts[dom]}</b></span>")
    parts.append("</div>")

    parts.append(_charts(ds))                     # inline SVG analytics charts

    for dom in REPORT_DOMAINS:
        if dom not in counts:
            continue
        parts.append(f"<h2>{_esc(DOMAIN_LABELS.get(dom, dom))} ({counts[dom]})</h2>")
        parts.append(_table(ds, dom, limit=table_limit))

    # manifest hash appendix (first/sample for verification reference)
    parts.append("<h2>Input manifest (SHA-256)</h2><table><thead><tr>"
                 "<th>file</th><th>size</th><th>sha256</th></tr></thead><tbody>")
    for e in files:
        parts.append(f"<tr><td class='prov'>{_esc(e.get('rel_path'))}</td>"
                     f"<td>{_esc(e.get('size'))}</td>"
                     f"<td class='prov'>{_esc(e.get('sha256'))}</td></tr>")
    parts.append("</tbody></table>")
    parts.append("</body></html>")
    ds.close()
    return "".join(parts)


def _pdf_via_qt(html_doc: str, out_path: str) -> bool:
    """Render HTML to PDF using Qt WebEngine (offline). Returns True on success."""
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtCore import QUrl, QEventLoop, QTimer
    except Exception:
        return False
    app = QApplication.instance() or QApplication([])
    view = QWebEngineView()
    done = {"ok": False}
    loop = QEventLoop()

    def _loaded(ok):
        def _written(path):
            done["ok"] = bool(path)
            loop.quit()
        view.page().printToPdf(out_path)
        # printToPdf writes asynchronously; also listen for the signal
        view.page().pdfPrintingFinished.connect(lambda p, s: _written(p))

    view.loadFinished.connect(_loaded)
    view.setHtml(html_doc, QUrl("https://localhost/"))
    QTimer.singleShot(15000, loop.quit)             # safety timeout
    loop.exec()
    import os as _os
    return _os.path.exists(out_path) and _os.path.getsize(out_path) > 0


def generate(case_dir: str, out_path: str, fmt: str = "html",
             table_limit: int | None = None) -> str:
    """Write the report. Returns the SHA-256 of the produced file."""
    doc = build_html(case_dir, table_limit=table_limit)
    if fmt == "html":
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(doc)
    elif fmt == "pdf":
        try:
            import weasyprint  # noqa
            weasyprint.HTML(string=doc).write_pdf(out_path)
        except ImportError:
            if not _pdf_via_qt(doc, out_path):       # fallback: Qt WebEngine print-to-PDF
                raise RuntimeError(
                    "PDF output needs WeasyPrint or PySide6-WebEngine. "
                    "Otherwise generate the HTML report and print to PDF.")
    else:
        raise ValueError(f"unsupported report format: {fmt}")

    # report self-integrity sidecar (EV-2)
    h = hashlib.sha256()
    with open(out_path, "rb") as f:
        h.update(f.read())
    digest = h.hexdigest()
    with open(out_path + ".sha256", "w", encoding="utf-8") as f:
        f.write(f"{digest}  {os.path.basename(out_path)}\n")
    return digest
