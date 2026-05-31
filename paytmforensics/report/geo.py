"""Export location fixes as KML and GeoJSON (offline) for mapping tools / court exhibits."""
from __future__ import annotations

import json
import xml.sax.saxutils as sx


def _fixes_from_casedb(casedb_path: str) -> list[dict]:
    import sqlite3
    con = sqlite3.connect(casedb_path)
    out = []
    for (data,) in con.execute("SELECT data FROM records WHERE domain IN ('location','diagnostic')"):
        d = json.loads(data)
        lat, lon = d.get("latitude"), d.get("longitude")
        if lat is None or lon is None:
            continue
        out.append({
            "lat": lat, "lon": lon,
            "src": d.get("source_kind") or ("diagnostic" if d.get("domain") == "diagnostic" else "signal"),
            "ts": (d.get("timestamp") or {}).get("utc_iso") or "",
        })
    con.close()
    out.sort(key=lambda p: p["ts"])
    return out


def to_geojson(fixes: list[dict]) -> str:
    features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [f["lon"], f["lat"]]},
        "properties": {"source": f["src"], "timestamp_utc": f["ts"]},
    } for f in fixes]
    if len(fixes) > 1:
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString",
                         "coordinates": [[f["lon"], f["lat"]] for f in fixes]},
            "properties": {"name": "movement_path"},
        })
    return json.dumps({"type": "FeatureCollection", "features": features}, indent=2)


def to_kml(fixes: list[dict]) -> str:
    marks = []
    for f in fixes:
        nm = sx.escape(f["ts"][:19] or f["src"])
        marks.append(
            f'<Placemark><name>{nm}</name>'
            f'<description>{sx.escape(f["src"])} | {sx.escape(f["ts"])}</description>'
            f'<Point><coordinates>{f["lon"]},{f["lat"]},0</coordinates></Point></Placemark>')
    path = ""
    if len(fixes) > 1:
        coords = " ".join(f'{f["lon"]},{f["lat"]},0' for f in fixes)
        path = (f'<Placemark><name>Movement path</name><LineString>'
                f'<coordinates>{coords}</coordinates></LineString></Placemark>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            '<name>PaytmForensics location fixes</name>'
            + "".join(marks) + path + '</Document></kml>')


def export(casedb_path: str, out_path: str, fmt: str) -> int:
    fixes = _fixes_from_casedb(casedb_path)
    doc = to_geojson(fixes) if fmt == "geojson" else to_kml(fixes) if fmt == "kml" else None
    if doc is None:
        raise ValueError(f"unsupported geo format: {fmt}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return len(fixes)
