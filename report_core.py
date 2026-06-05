from __future__ import annotations

import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET

import fitz
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, PageBreak, Paragraph, Spacer, Table,
    TableStyle, KeepTogether
)

PAGE_W, PAGE_H = A4
LEFT = 13 * mm
RIGHT = 13 * mm
TOP = 10 * mm
BOTTOM = 10 * mm
FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"

TTS_RED = colors.HexColor("#CC0000")
TTS_SEAFOAM = colors.HexColor("#99CCCC")
TTS_CHARCOAL = colors.HexColor("#312F2F")
TTS_BEIGE = colors.HexColor("#E5E6DC")
TTS_WHITE = colors.HexColor("#F7F7F7")
LIGHT_SEAFOAM = colors.HexColor("#D6EBEB")
LIGHT_GREY = colors.HexColor("#F2F2EC")
GRID = colors.HexColor("#8F908A")
PASS_GREEN = colors.HexColor("#E0F0F0")
FAIL_RED = colors.HexColor("#F3CCCC")


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("TitleMain", parent=styles["Title"], fontName=FONT_BOLD, fontSize=19, leading=22, textColor=TTS_CHARCOAL, spaceAfter=4))
    styles.add(ParagraphStyle("Section", parent=styles["Normal"], fontName=FONT_BOLD, fontSize=9, leading=11, textColor=TTS_WHITE, uppercase=True))
    styles.add(ParagraphStyle("Label", parent=styles["Normal"], fontName=FONT_BOLD, fontSize=7, leading=8.5, textColor=TTS_CHARCOAL))
    styles.add(ParagraphStyle("Body", parent=styles["Normal"], fontName=FONT, fontSize=8, leading=10, textColor=TTS_CHARCOAL))
    styles.add(ParagraphStyle("BodySmall", parent=styles["Normal"], fontName=FONT, fontSize=6.8, leading=8, textColor=TTS_CHARCOAL))
    styles.add(ParagraphStyle("BodyBold", parent=styles["Normal"], fontName=FONT_BOLD, fontSize=8, leading=10, textColor=TTS_CHARCOAL))
    styles.add(ParagraphStyle("BlueHeading", parent=styles["Normal"], fontName=FONT_BOLD, fontSize=8, leading=10, textColor=TTS_CHARCOAL))
    return styles


def _esc(text: Any) -> str:
    if text is None:
        return ""
    s = str(text).replace("\n", "<br/>")
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("&lt;br/&gt;", "<br/>")


def _p(text: Any, style) -> Paragraph:
    return Paragraph(_esc(text), style)


def _section(title: str, styles) -> Table:
    t = Table([[_p(title, styles["Section"])]], colWidths=[PAGE_W - LEFT - RIGHT], rowHeights=[7 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), TTS_RED),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def _status_from_code(code: Optional[str]) -> str:
    if code is None or code == "":
        return ""
    return "Pass" if str(code).strip() == "1" else "Fail"


def _legacy_status_from_code(code: Optional[str]) -> str:
    """Status mapping used by older aPAT / ES Manager exports.

    In newer PADFX measurement trees, S=1 is Pass. In older files exported
    through aPAT/APX/ES Manager, direct measurement records commonly use S=0
    for pass and non-zero values for failed/invalid results.
    """
    if code is None or code == "":
        return ""
    return "Pass" if str(code).strip() == "0" else "Fail"


def _parse_dt(value: str) -> Tuple[datetime, str]:
    value = (value or "").strip()
    if not value:
        return datetime.min, ""
    for fmt in ["%d.%m.%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d"]:
        try:
            dt = datetime.strptime(value, fmt)
            return dt, dt.strftime("%d/%m/%Y")
        except ValueError:
            pass
    return datetime.min, value.replace(".", "/")


def _date_only(value: str) -> Optional[date]:
    dt, _ = _parse_dt(value)
    if dt == datetime.min:
        return None
    return dt.date()


def _in_date_range(d: Optional[date], start_date: Optional[str], end_date: Optional[str]) -> bool:
    if d is None:
        return not (start_date or end_date)
    start = _date_only(start_date or "") if start_date else None
    end = _date_only(end_date or "") if end_date else None
    if start and d < start:
        return False
    if end and d > end:
        return False
    return True


def _text(el: Optional[ET.Element], default: str = "") -> str:
    return (el.text or default).strip() if el is not None else default


def _children_map(parent: ET.Element, tag: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in parent.findall(f"./{tag}s/{tag}"):
        iid = item.attrib.get("Id", "")
        val = _text(item.find("V"))
        if iid and val:
            out[iid] = val
    return out


def _all_pairs(parent: ET.Element, tag: str) -> List[Tuple[str, str, str]]:
    pairs = []
    for item in parent.findall(f"./{tag}s/{tag}"):
        iid = item.attrib.get("Id", "")
        val = _text(item.find("V"))
        status = _status_from_code(_text(item.find("S")))
        if iid or val:
            pairs.append((iid, val, status))
    return pairs


P_MAP = {
    "194": "Appliance ID",
    "195": "Type",
    "212": "Location",
    "257": "Retest Date",
    "261": "Test Date",
}

VISUAL_ITEMS = {
    "30": "wiring connection points",
    "31": "cables",
    "32": "covers, housing",
    "33": "inscriptions and markings",
}

MID_NAMES = {
    "66": "Visual",
    "68": "Continuity",
    "69": "R iso EE",
    "70": "R iso EE",
    "73": "RCD",
    "74": "Touch leakage",
    "75": "Differential Leakage",
    "79": "Polarity",
    "80": "Continuity",
    "118": "R iso EE",
    "147": "Polarity",
    "160": "RCD",
    "159": "RCD",
    "289": "Leakage",
    "290": "Touch leakage",
}

MP_LABELS = {
    "1": "DateTime", "119": "Output", "66": "I out", "69": "Duration", "161": "Type", "4": "Uiso",
    "155": "Mode", "251": "Status", "327": "LN cross", "157": "Mode", "20": "RCD type", "75": "IΔN",
    "76": "Multiplier", "255": "Random phase", "21": "Phase", "14": "RCD Standard", "78": "RCD Standard",
    "210": "Mains polarity", "479": "Mode", "517": "Adapter", "347": "Adapter",
}

R_LABELS = {
    "135": "R", "139": "Riso", "140": "Riso-S", "10": "Um", "151": "Result",
    "358": "t IΔN, (+)", "359": "t IΔN, (-)", "114": "Uc", "676": "Idiff TRMS", "78": "P",
    "695": "Itou TRMS", "696": "Itou AC", "697": "Itou DC",
}

L_LABELS = {
    "40": "H Limit (R)",
    "41": "L Limit (Riso)",
    "43": "H Limit (R)", "46": "L Limit (Riso)", "47": "L Limit (Riso-S)", "48": "L Limit (Riso-S)",
    "6": "Limit Uc (Uc)", "55": "H Limit", "57": "H Limit", "244": "Limit", "245": "Limit",
}


def _format_pairs(pairs: Iterable[Tuple[str, str]]) -> str:
    return "<br/>".join(f"{k}: {v}" for k, v in pairs if v)


def _shorten(text: str, max_chars: int = 160) -> str:
    text = " ".join((text or "").replace("<br/>", "; ").split())
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


def _parse_padfx_xml(path: str | Path) -> ET.Element:
    path = Path(path)
    if path.suffix.lower() == ".padf":
        return ET.parse(path).getroot()
    with zipfile.ZipFile(path, "r") as zf:
        names = zf.namelist()
        ds = next((n for n in names if n.lower().endswith("datasource.padf")), None)
        if not ds:
            raise ValueError("The .padfx file does not contain DataSource.padf")
        data = zf.read(ds)
    return ET.fromstring(data.decode("utf-8-sig", errors="replace"))


def _instrument_map(root: ET.Element) -> Dict[str, Dict[str, str]]:
    out = {}
    for inst in root.findall(".//I"):
        guid = _text(inst.find("IGuId"))
        if guid:
            out[guid] = {
                "Model": _text(inst.find("IMiNm")),
                "Name": _text(inst.find("INm")),
                "Serial": _text(inst.find("ISer")),
                "Firmware": _text(inst.find("ISwV")),
                "CalDate": _text(inst.find("ICalD")),
            }
    return out


def _user_map(root: ET.Element) -> Dict[str, str]:
    return {_text(u.find("UGuId")): _text(u.find("N")) for u in root.findall(".//U") if _text(u.find("UGuId"))}


def _so_maps(root: ET.Element):
    so_by_id: Dict[str, ET.Element] = {}
    parent_by_id: Dict[str, str] = {}
    name_by_id: Dict[str, str] = {}
    for so in root.findall(".//SO"):
        sid = so.attrib.get("Id", "")
        so_by_id[sid] = so
        parent_by_id[sid] = _text(so.find("PID"))
        name_by_id[sid] = _text(so.find("N"))
    return so_by_id, parent_by_id, name_by_id


def _structure_path(sid: str, parent_by_id: Dict[str, str], name_by_id: Dict[str, str]) -> str:
    parts = []
    cur = sid
    seen = set()
    while cur and cur not in seen:
        seen.add(cur)
        name = name_by_id.get(cur, "")
        if name:
            parts.append(name)
        cur = parent_by_id.get(cur, "")
        if cur == "-1":
            break
    return "/".join(reversed(parts))


def _properties(so: ET.Element) -> Dict[str, str]:
    props: Dict[str, str] = {}
    for p in so.findall("./Ps/P"):
        pid = p.attrib.get("Id", "")
        if pid in P_MAP and _text(p.find("V")):
            props[P_MAP[pid]] = _text(p.find("V"))
    return props


def _visual_test(it: ET.Element, category: str, users: Dict[str, str], instruments: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    dt_raw = _text(it.find("DT"))
    user = users.get(_text(it.find("UGuId")), "")
    status = _status_from_code(_text(it.find("S"))) or "Recorded"
    rows = []
    for ib in it.findall("./IBs/IB"):
        iid = ib.attrib.get("Id", "")
        label = VISUAL_ITEMS.get(iid, f"Item {iid}")
        val = _text(ib.find("V"))
        rows.append((label, "Pass" if val == "5" else val))
    instrument_guid = _text(it.find("IGuId"))
    instrument = instruments.get(instrument_guid, {})
    results = _format_pairs(rows)
    return {
        "Category": f"{category} - Visual Inspections",
        "Measurement": "Visual",
        "Status": status,
        "DateTime": dt_raw,
        "Date": _parse_dt(dt_raw)[1],
        "User": user,
        "Results": results,
        "Limits": "",
        "Parameters": _format_pairs([("User", user), ("Instrument", instrument.get("Model", "")), ("Serial", instrument.get("Serial", ""))]),
        "CompactResult": "Visual inspection: " + (status or "Recorded"),
        "CompactLimit": "",
        "CompactParams": f"User: {user}" if user else "",
    }


def _measurement_test(m: ET.Element, category: str, users: Dict[str, str], instruments: Dict[str, Dict[str, str]], legacy: bool = False) -> Dict[str, str]:
    mid = _text(m.find("MID"))
    name = MID_NAMES.get(mid, f"Measurement {mid}")
    status = (_legacy_status_from_code(_text(m.find("S"))) if legacy else _status_from_code(_text(m.find("S")))) or "Recorded"
    mps = _children_map(m, "MP")
    dt_raw = mps.get("1", "")
    user = users.get(_text(m.find("UGuId")), "")
    instrument_guid = _text(m.find("./IGuIds/IGuId")) or _text(m.find("IGuId"))
    instrument = instruments.get(instrument_guid, {})

    results = []
    for iid, val, rstatus in _all_pairs(m, "R"):
        label = R_LABELS.get(iid, f"R{iid}")
        results.append((label, val))
    limits = []
    for iid, val, _ in _all_pairs(m, "L"):
        limits.append((L_LABELS.get(iid, f"Limit {iid}"), val))
    params = []
    for iid, val in mps.items():
        if iid in {"2", "3"}:
            continue
        params.append((MP_LABELS.get(iid, f"Param {iid}"), val))
    if user:
        params.append(("User", user))
    if instrument.get("Model"):
        params.append(("Instrument", instrument.get("Model", "")))
    if instrument.get("Serial"):
        params.append(("Serial", instrument.get("Serial", "")))

    compact_result = "; ".join(f"{k}: {v}" for k, v in results[:4]) or status
    compact_limit = "; ".join(f"{k}: {v}" for k, v in limits[:3])
    compact_params = "; ".join(f"{k}: {v}" for k, v in params[:5] if k != "DateTime")
    return {
        "Category": f"{category} - Single tests",
        "Measurement": name,
        "Status": status,
        "DateTime": dt_raw,
        "Date": _parse_dt(dt_raw)[1],
        "User": user,
        "Results": _format_pairs(results),
        "Limits": _format_pairs(limits),
        "Parameters": _format_pairs(params),
        "CompactResult": compact_result,
        "CompactLimit": compact_limit,
        "CompactParams": compact_params,
    }


def _extract_tests(so: ET.Element, users: Dict[str, str], instruments: Dict[str, Dict[str, str]]) -> List[Dict[str, str]]:
    tests: List[Dict[str, str]] = []
    # Older aPAT/ES Manager exports store measurements directly as SO/Ms/M.
    for m in so.findall("./Ms/M"):
        # aPAT legacy PADFX measurements contain RawIV/N and use S=0 as pass.
        # ES Manager PADFX direct measurements usually use modern S=1 pass.
        is_legacy_m = m.find("RawIV") is not None or m.find("N") is not None
        tests.append(_measurement_test(m, "Tester export", users, instruments, legacy=is_legacy_m))
    for at in so.findall("./Ms/AT"):
        category = _text(at.find("./AT_Header/N")) or "Auto test"
        for cmd in at.findall(".//Command[@type='measurement']"):
            for it in cmd.findall("./Ms/IT"):
                tests.append(_visual_test(it, category, users, instruments))
            for m in cmd.findall("./Ms/M"):
                tests.append(_measurement_test(m, category, users, instruments))
    return tests


def _filter_tests_by_mode(tests: List[Dict[str, str]], filter_mode: str = "latest", start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict[str, str]]:
    mode = (filter_mode or "latest").lower()
    if mode == "all":
        return tests
    if mode == "range":
        return [t for t in tests if _in_date_range(_date_only(t.get("DateTime", "")), start_date, end_date)]
    # latest: latest calendar date per asset. If range supplied, latest inside range.
    filtered = tests
    if start_date or end_date:
        filtered = [t for t in tests if _in_date_range(_date_only(t.get("DateTime", "")), start_date, end_date)]
    dates = [_date_only(t.get("DateTime", "")) for t in filtered]
    dates = [d for d in dates if d]
    if not dates:
        return filtered
    latest = max(dates)
    return [t for t in filtered if _date_only(t.get("DateTime", "")) == latest]


def _asset_status(tests: Sequence[Dict[str, str]]) -> str:
    if not tests:
        return "Recorded"
    return "Fail" if any((t.get("Status", "").lower() == "fail") for t in tests) else "Pass"


def _asset_test_summary(row: Dict[str, Any], max_chars: int = 150) -> str:
    tests = row.get("Tests", []) or []
    parts = []
    for t in tests[:8]:
        parts.append(f"{t.get('Measurement','Test')}: {t.get('CompactResult') or t.get('Status') or 'Recorded'}")
    if len(tests) > 8:
        parts.append(f"+{len(tests)-8} more")
    return _shorten("; ".join(parts), max_chars) if parts else "No test rows"




def _project_source_type(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return "aPAT PDF"
    if ext == ".apx":
        return "APX"
    if ext == ".xlsx":
        return "XLSX"
    if ext == ".padf":
        return "PADF"
    return "PADFX"


def _is_supported_project_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in {".pdf", ".padfx", ".padf", ".apx", ".xlsx"}


def _decode_legacy_raw(raw: str) -> str:
    raw = raw or ""
    try:
        if re.fullmatch(r"[0-9A-Fa-f]+", raw) and len(raw) % 2 == 0:
            return bytes.fromhex(raw).decode("latin1", errors="replace")
    except Exception:
        pass
    return raw


def _legacy_raw_status(raw: str) -> str:
    decoded = _decode_legacy_raw(raw)
    bad_markers = ["---", ">1999", "FAIL", "FAILED"]
    return "Fail" if any(m in decoded.upper() for m in bad_markers) else "Pass"


APX_MEASUREMENT_NAMES = {
    "66": "Visual",
    "68": "Continuity",
    "69": "R iso EE",
    "70": "R iso EE",
    "73": "RCD",
    "74": "Touch leakage",
    "75": "Differential Leakage",
    "79": "Polarity",
}


def _apx_props(params: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    props: Dict[str, str] = {}
    for p in params or []:
        pid = str(p.get("Id", ""))
        val = str(p.get("V", "") or "").strip()
        if pid in P_MAP and val:
            props[P_MAP[pid]] = val
    return props


def _json_structure_path(guid: str, by_id: Dict[str, Dict[str, Any]]) -> str:
    parts = []
    cur = guid
    seen = set()
    while cur and cur not in seen and cur in by_id:
        seen.add(cur)
        name = str(by_id[cur].get("name", "") or "")
        if name:
            parts.append(name)
        cur = str(by_id[cur].get("parentId", "") or "")
        if cur == "-1":
            break
    return "/".join(reversed(parts))


def _load_apx_rows(path: str | Path, filter_mode: str = "latest", start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict[str, Any]]:
    with zipfile.ZipFile(path, "r") as zf:
        names = zf.namelist()
        struct_name = next((n for n in names if n.lower().endswith("strucutureexport.json") or n.lower().endswith("structureexport.json")), None)
        inst_name = next((n for n in names if n.lower().endswith("instrumentsexport.json")), None)
        if not struct_name:
            raise ValueError("The .apx file does not contain StrucutureExport.json")
        structures = json.loads(zf.read(struct_name).decode("utf-8-sig", errors="replace"))
        instruments_raw = json.loads(zf.read(inst_name).decode("utf-8-sig", errors="replace")) if inst_name else []
    instruments = {str(i.get("GuId", "")): {"Model": str(i.get("MiName", "") or i.get("Name", "")), "Serial": str(i.get("SerialNumber", "")), "Firmware": str(i.get("SwVersion", ""))} for i in instruments_raw}
    by_id = {str(o.get("guid", "")): o for o in structures}
    rows: List[Dict[str, Any]] = []
    for obj in structures:
        measurements = obj.get("structureMeasurements") or []
        props = _apx_props(obj.get("structureParameters") or [])
        is_asset = bool(measurements) or bool(props.get("Appliance ID"))
        if not is_asset:
            continue
        path_str = _json_structure_path(str(obj.get("guid", "")), by_id)
        tests_all: List[Dict[str, str]] = []
        for m in measurements:
            base = str(m.get("baseObjectId", ""))
            raw = str(m.get("oldRawString", "") or "")
            decoded = _decode_legacy_raw(raw)
            status = _legacy_raw_status(raw)
            inst = instruments.get(str(m.get("structureInstrumentGuId", "")), {})
            dt_raw = props.get("Test Date", "")
            tests_all.append({
                "Category": "APX tester export",
                "Measurement": APX_MEASUREMENT_NAMES.get(base, f"Measurement {base}"),
                "Status": status,
                "DateTime": dt_raw,
                "Date": _parse_dt(dt_raw)[1],
                "User": "",
                "Results": decoded,
                "Limits": "",
                "Parameters": _format_pairs([("Instrument", inst.get("Model", "")), ("Serial", inst.get("Serial", "")), ("Raw", decoded)]),
                "CompactResult": decoded,
                "CompactLimit": "",
                "CompactParams": f"Serial: {inst.get('Serial', '')}" if inst.get("Serial") else "",
            })
        tests = _filter_tests_by_mode(tests_all, filter_mode=filter_mode, start_date=start_date, end_date=end_date)
        if tests_all and not tests:
            continue
        _, test_display = _parse_dt(props.get("Test Date", ""))
        _, retest_display = _parse_dt(props.get("Retest Date", ""))
        location = props.get("Location", "")
        if not location:
            parts = path_str.split("/")
            location = parts[1] if len(parts) > 1 else ""
        serial = ""
        model = ""
        for inst in instruments.values():
            if inst.get("Serial"):
                serial = inst.get("Serial", "")
                model = inst.get("Model", "")
                break
        rows.append({
            "Appliance ID": props.get("Appliance ID") or str(obj.get("name", "")),
            "Type": props.get("Type") or str(obj.get("name", "")),
            "Location": location,
            "Test Date": test_display,
            "Retest Date": retest_display,
            "Status": _asset_status(tests),
            "User": "",
            "Structure Path": path_str,
            "Comment": "Imported from APX",
            "Tests": tests,
            "Serial": serial,
            "Instrument": model,
        })
    rows.sort(key=lambda r: (0 if r.get("Tests") else 1, r.get("Location", ""), r.get("Appliance ID", "")))
    return rows


def _xlsx_col_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha())
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch.upper()) - 64)
    return idx - 1


def _xlsx_sheet_rows(path: str | Path, sheet_path: str) -> List[List[str]]:
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path, "r") as zf:
        root = ET.fromstring(zf.read(sheet_path))
    rows: List[List[str]] = []
    for row in root.findall(".//a:sheetData/a:row", ns):
        vals: List[str] = []
        for c in row.findall("a:c", ns):
            idx = _xlsx_col_index(c.attrib.get("r", "A1"))
            while len(vals) <= idx:
                vals.append("")
            v = c.find("a:v", ns)
            inline = c.find("a:is/a:t", ns)
            vals[idx] = (v.text if v is not None and v.text is not None else inline.text if inline is not None and inline.text is not None else "")
        rows.append(vals)
    return rows


def _dedupe_headers(headers: Sequence[str]) -> List[str]:
    counts: Dict[str, int] = {}
    out: List[str] = []
    for h in headers:
        base = (h or "").strip() or "Unnamed"
        n = counts.get(base, 0)
        out.append(base if n == 0 else f"{base}.{n}")
        counts[base] = n + 1
    return out


def _row_get(row: Dict[str, str], *names: str) -> str:
    low = {k.lower(): v for k, v in row.items()}
    for name in names:
        if name.lower() in low:
            return low[name.lower()]
    return ""


def _xlsx_test_from_row(row: Dict[str, str], visual_map: Dict[str, List[str]]) -> Dict[str, str]:
    measurement = _row_get(row, "Measurement") or "Test"
    status = _row_get(row, "Status") or "Recorded"
    dt_raw = _row_get(row, "DateTime")
    result_cols = ["Um", "S", "t IΔN x1, (+)", "t IΔN x1, (-)", "t IΔN, (+)", "t IΔN, (-)", "R", "Riso", "Idiff", "Itou", "Result", "Riso-S", "Uc", "P"]
    limit_cols = ["H Limit", "L Limit", "L Limit.1", "Limit Uc", "H Limit.1", "H Limit.2"]
    param_cols = ["Output", "I out", "Duration", "Uiso", "Mode", "Type", "RCD Standard", "RCD type", "IΔN", "Multiplier", "Phase", "Mains polarity", "Instrument ID", "FW ID", "Name", "Serial", "Firmware version"]
    if measurement.lower() == "visual":
        vals = visual_map.get(_row_get(row, "Structure Path"), [])
        results = "; ".join(vals) if vals else "Visual inspection: " + status
        compact_result = status
    else:
        pairs = [(c, _row_get(row, c)) for c in result_cols if _row_get(row, c)]
        results = _format_pairs(pairs)
        compact_result = "; ".join(f"{k}: {v}" for k, v in pairs[:4]) or status
    limit_pairs = [(c, _row_get(row, c)) for c in limit_cols if _row_get(row, c)]
    param_pairs = [(c, _row_get(row, c)) for c in param_cols if _row_get(row, c)]
    return {
        "Category": "XLSX tester export",
        "Measurement": measurement,
        "Status": status,
        "DateTime": dt_raw,
        "Date": _parse_dt(dt_raw)[1],
        "User": "",
        "Results": results,
        "Limits": _format_pairs(limit_pairs),
        "Parameters": _format_pairs(param_pairs),
        "CompactResult": compact_result,
        "CompactLimit": "; ".join(f"{k}: {v}" for k, v in limit_pairs[:3]),
        "CompactParams": "; ".join(f"{k}: {v}" for k, v in param_pairs[:5]),
    }


def _load_xlsx_rows(path: str | Path, filter_mode: str = "latest", start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict[str, Any]]:
    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())
        if "xl/worksheets/sheet1.xml" not in names:
            raise ValueError("The .xlsx file does not contain the expected ES Manager sheet1.xml")
    sheet1 = _xlsx_sheet_rows(path, "xl/worksheets/sheet1.xml")
    sheet2 = _xlsx_sheet_rows(path, "xl/worksheets/sheet2.xml") if "xl/worksheets/sheet2.xml" in names else []
    if not sheet1:
        raise ValueError("The .xlsx file does not contain any result rows")
    headers = _dedupe_headers(sheet1[0])
    data_rows = []
    for vals in sheet1[1:]:
        vals = vals + [""] * (len(headers) - len(vals))
        row = {headers[i]: vals[i].strip() if isinstance(vals[i], str) else str(vals[i]) for i in range(len(headers))}
        if any(row.values()):
            data_rows.append(row)
    visual_map: Dict[str, List[str]] = {}
    if sheet2:
        vh = _dedupe_headers(sheet2[0])
        for vals in sheet2[1:]:
            vals = vals + [""] * (len(vh) - len(vals))
            r = {vh[i]: vals[i].strip() if isinstance(vals[i], str) else str(vals[i]) for i in range(len(vh))}
            sp = _row_get(r, "Structure Path")
            inspection = _row_get(r, "Inspection")
            val = _row_get(r, "Value")
            if sp and inspection:
                visual_map.setdefault(sp, []).append(f"{inspection}: {val}")
    grouped: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for row in data_rows:
        aid = _row_get(row, "Appliance Id", "Appliance ID")
        if not aid:
            continue
        if aid not in grouped:
            order.append(aid)
            sp = _row_get(row, "Structure Path")
            parts = sp.split("/") if sp else []
            grouped[aid] = {
                "Appliance ID": aid,
                "Type": _row_get(row, "Appliance name", "Type"),
                "Location": parts[1] if len(parts) > 1 else "",
                "Test Date": "",
                "Retest Date": "",
                "Status": "Recorded",
                "User": "",
                "Structure Path": sp,
                "Comment": "Imported from ES Manager XLSX",
                "Tests": [],
                "Serial": _row_get(row, "Serial"),
                "Instrument": _row_get(row, "Name"),
            }
        grouped[aid]["Tests"].append(_xlsx_test_from_row(row, visual_map))
    rows: List[Dict[str, Any]] = []
    for aid in order:
        r = grouped[aid]
        tests_all = r.get("Tests", []) or []
        tests = _filter_tests_by_mode(tests_all, filter_mode=filter_mode, start_date=start_date, end_date=end_date)
        if tests_all and not tests:
            continue
        latest_dt = datetime.min
        latest_display = ""
        for t in tests:
            dt, disp = _parse_dt(t.get("DateTime", ""))
            if dt >= latest_dt:
                latest_dt = dt
                latest_display = disp
        r["Tests"] = tests
        r["Test Date"] = latest_display
        r["Status"] = _asset_status(tests)
        rows.append(r)
    rows.sort(key=lambda r: (0 if r.get("Tests") else 1, r.get("Location", ""), r.get("Appliance ID", "")))
    return rows




# -------- aPAT PDF parser --------
def _pdf_text(path: str | Path) -> str:
    """Extract selectable embedded text from the aPAT PDF export. No OCR is used."""
    doc = fitz.open(str(path))
    parts: List[str] = []
    for page in doc:
        parts.append(page.get_text("text"))
    return "\n".join(parts)


ASSET_STATUS_VALUES = {"PASS", "FAIL", "NO RESULT"}
ASSET_HEADER_RE = re.compile(r"^\s*(\d+)\s+(.+?/)\s+(PASS|FAIL|NO RESULT)\s*$", re.I)
TEST_LINE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 /_()\-+,]+?)\s+(\d{1,2}[./]\d{1,2}[./]\d{2,4}\s+\d{1,2}:\d{2}(?::\d{2})?)\s+(PASS|FAIL|NO RESULT)\s*$", re.I)


def _title_status(value: str) -> str:
    value = (value or "").strip().upper()
    if value == "NO RESULT":
        return "Recorded"
    return value.title()


def _pdf_asset_id_from_path(path_line: str) -> str:
    """Return the appliance segment from an aPAT PDF structure path.

    aPAT can export paths such as ``Test and/CAL Room//`` for assets with a
    blank appliance ID.  Using ``rstrip('/').split('/')[-1]`` turns that into
    the room name, which then makes blank-ID assets look like duplicates of the
    location.  Preserve the final blank segment instead.
    """
    parts = (path_line or "").split("/")
    if len(parts) >= 3:
        return parts[-2] if parts[-1] == "" else parts[-1]
    return ""


def _pdf_blocks(text: str) -> List[Tuple[Any, List[str]]]:
    lines = [ln.rstrip() for ln in text.splitlines()]
    # line index, source section number, structure path, appliance id, status, header line count
    headers: List[Tuple[int, int, str, str, str, int]] = []
    i = 0
    while i < len(lines) - 2:
        num = lines[i].strip()
        path_line = lines[i + 1].strip()
        status_line = lines[i + 2].strip().upper()
        if num.isdigit() and int(num) > 1 and path_line.endswith("/") and status_line in ASSET_STATUS_VALUES:
            aid = _pdf_asset_id_from_path(path_line)
            headers.append((i, int(num), path_line, aid, _title_status(status_line), 3))
            i += 3
            continue
        # Also support layout where the header appears on one line.
        m = ASSET_HEADER_RE.match(lines[i])
        if m and int(m.group(1)) > 1:
            path_line = m.group(2).strip()
            headers.append((i, int(m.group(1)), path_line, _pdf_asset_id_from_path(path_line), _title_status(m.group(3)), 1))
        i += 1

    blocks = []
    for idx, (line_i, n, path_line, aid, status, header_len) in enumerate(headers):
        start = line_i + header_len
        end = headers[idx + 1][0] if idx + 1 < len(headers) else len(lines)
        dummy = type("PdfHeader", (), {"group": lambda self, k, _n=n, _p=path_line, _a=aid, _s=status: {1: str(_n), 2: _p, 3: _a, 4: _s}[k]})()
        blocks.append((dummy, [ln.strip() for ln in lines[start:end]]))
    return blocks


def _parse_pdf_tests(lines: List[str]) -> List[Dict[str, str]]:
    tests: List[Dict[str, str]] = []
    current: Optional[Dict[str, str]] = None
    buffer: List[str] = []

    def flush() -> None:
        nonlocal current, buffer
        if not current:
            return
        results: List[str] = []
        params: List[str] = []
        mode = None
        for raw in buffer:
            b = raw.strip()
            if not b:
                continue
            if b == "RESULTS":
                mode = "results"
                continue
            if b == "PARAMETERS":
                mode = "params"
                continue
            if b.startswith("Instrument"):
                current["Instrument"] = b.split(":", 1)[-1].strip()
            elif mode == "params" or "Duration" in b or "Output" in b or "Uiso" in b or "Mode" in b or "I out" in b:
                params.append(b)
            elif mode == "results" or ":" in b:
                results.append(b)
        current["Results"] = " | ".join(results[:12])
        current["Parameters"] = " | ".join(params[:12])
        current["CompactResult"] = current["Results"] or current["Status"]
        current["CompactParams"] = current["Parameters"]
        tests.append(current)
        current = None
        buffer = []

    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        m = TEST_LINE_RE.match(ln)
        if m:
            status = _title_status(m.group(3))
            dt_raw = m.group(2)
            meas = m.group(1).strip()
        else:
            # Some PDF text extraction puts PASS/FAIL on the next line.
            m2 = re.match(r"^\s*([A-Za-z][A-Za-z0-9 /_()\-+,]+?)\s+(\d{1,2}[./]\d{1,2}[./]\d{2,4}\s+\d{1,2}:\d{2}(?::\d{2})?)\s*$", ln)
            if m2 and i + 1 < len(lines) and lines[i + 1].strip().upper() in ASSET_STATUS_VALUES:
                status = _title_status(lines[i + 1].strip())
                dt_raw = m2.group(2)
                meas = m2.group(1).strip()
                i += 1
            else:
                if current:
                    buffer.append(ln)
                i += 1
                continue
        flush()
        _, disp = _parse_dt(dt_raw)
        current = {
            "Category": "aPAT PDF export",
            "Measurement": meas,
            "Status": status,
            "DateTime": dt_raw,
            "Date": disp,
            "User": "",
            "Results": "",
            "Limits": "",
            "Parameters": "",
            "CompactResult": status,
            "CompactLimit": "",
            "CompactParams": "",
        }
        i += 1
    flush()
    return tests


def _load_pdf_rows(path: str | Path, filter_mode: str = "latest", start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict[str, Any]]:
    text = _pdf_text(path)
    rows: List[Dict[str, Any]] = []
    for match, lines in _pdf_blocks(text):
        full_path = match.group(2).strip()
        aid = match.group(3).strip()
        status = match.group(4).title()
        name = ""
        loc = ""
        retest = ""
        test_code = ""
        user = ""
        serial = ""
        for ln in lines:
            if ln.startswith("Name:"):
                name = ln.split(":", 1)[1].strip()
            elif "(Room) Location:" in ln:
                loc = ln.split(":", 1)[1].strip()
            elif ln.startswith("Next test of appliance:"):
                retest = _parse_dt(ln.split(":", 1)[1].strip())[1]
            elif ln.startswith("Test code:"):
                test_code = ln.split(":", 1)[1].strip()
            elif ln.startswith("User:"):
                user = ln.split(":", 1)[1].strip()
            elif ln.startswith("Instrument :"):
                serial = ln.split(":", 1)[1].strip()
        # Keep blank-name appliance records. aPAT can export valid assets with an
        # empty Name field, including blank-ID CAL Room records and NO RESULT
        # records.  Folder rows are already excluded by _pdf_blocks because they
        # do not have a PASS/FAIL/NO RESULT status line.
        tests_all = _parse_pdf_tests(lines)
        for t in tests_all:
            if not t.get("User") and user:
                t["User"] = user
        tests = _filter_tests_by_mode(tests_all, filter_mode=filter_mode, start_date=start_date, end_date=end_date)
        if tests_all and not tests:
            continue
        latest_dt = datetime.min
        latest_disp = ""
        for t in tests:
            dt, disp = _parse_dt(t.get("DateTime", ""))
            if dt >= latest_dt:
                latest_dt, latest_disp = dt, disp
            if not user and t.get("User"):
                user = t.get("User", "")
        rows.append({
            "Appliance ID": aid,
            "Type": name,
            "Location": loc,
            "Test Date": latest_disp,
            "Retest Date": retest,
            "Status": _asset_status(tests) if tests else status,
            "User": user,
            "Structure Path": full_path,
            "Comment": f"Imported from aPAT PDF - {test_code}",
            "Tests": tests,
            "Serial": serial,
            "Instrument": serial,
        })
    rows.sort(key=lambda r: (0 if r.get("Tests") else 1, r.get("Location", ""), r.get("Appliance ID", "")))
    return rows


def load_project_rows(path: str | Path, filter_mode: str = "latest", start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict[str, Any]]:
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return _load_pdf_rows(path, filter_mode=filter_mode, start_date=start_date, end_date=end_date)
    if ext in {".padfx", ".padf"}:
        return load_padfx_rows(path, filter_mode=filter_mode, start_date=start_date, end_date=end_date)
    if ext == ".apx":
        return _load_apx_rows(path, filter_mode=filter_mode, start_date=start_date, end_date=end_date)
    if ext == ".xlsx":
        return _load_xlsx_rows(path, filter_mode=filter_mode, start_date=start_date, end_date=end_date)
    raise ValueError("Unsupported file type. Please upload .pdf, .padfx, .apx or .xlsx")


def load_padfx_rows(path: str | Path, filter_mode: str = "latest", start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict[str, Any]]:
    root = _parse_padfx_xml(path)
    users = _user_map(root)
    instruments = _instrument_map(root)
    so_by_id, parent_by_id, name_by_id = _so_maps(root)
    rows: List[Dict[str, Any]] = []
    for sid, so in so_by_id.items():
        tests_all = _extract_tests(so, users, instruments)
        props = _properties(so)
        is_asset = bool(tests_all) or bool(props.get("Appliance ID"))
        if not is_asset:
            continue
        tests = _filter_tests_by_mode(tests_all, filter_mode=filter_mode, start_date=start_date, end_date=end_date)
        if tests_all and not tests:
            continue
        path_str = _structure_path(sid, parent_by_id, name_by_id)
        latest_dt = datetime.min
        _, latest_display = _parse_dt(props.get("Test Date", ""))
        latest_user = ""
        for t in tests:
            dt, disp = _parse_dt(t.get("DateTime", ""))
            if dt >= latest_dt:
                latest_dt = dt
                latest_display = disp
                latest_user = t.get("User", "")
        location = props.get("Location", "")
        if not location:
            parts = path_str.split("/")
            if len(parts) > 1:
                location = parts[1]
        instrument_serial = ""
        instrument_model = ""
        # Prefer the active MI 3340 / AlphaEE tester where present.
        for inst in instruments.values():
            if inst.get("Serial") == "24460062" or inst.get("Model") == "MI 3340":
                instrument_serial = inst.get("Serial", "")
                instrument_model = inst.get("Model", "") or "MI 3340"
                break
        if not instrument_serial:
            for inst in instruments.values():
                if inst.get("Serial"):
                    instrument_serial = inst["Serial"]
                    instrument_model = inst.get("Model", "")
                    break
        _, retest_display = _parse_dt(props.get("Retest Date", ""))
        rows.append({
            "Appliance ID": props.get("Appliance ID") or name_by_id.get(sid, ""),
            "Type": props.get("Type") or name_by_id.get(sid, ""),
            "Location": location,
            "Test Date": latest_display.replace(".", "/"),
            "Retest Date": retest_display,
            "Status": _asset_status(tests),
            "User": latest_user,
            "Structure Path": path_str,
            "Comment": "",
            "Tests": tests,
            "Serial": instrument_serial,
            "Instrument": instrument_model,
        })
    rows.sort(key=lambda r: (0 if r.get("Tests") else 1, r.get("Location", ""), r.get("Appliance ID", "")))
    return rows


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def preview_padfx(path: str | Path, filter_mode: str = "latest", start_date: Optional[str] = None, end_date: Optional[str] = None, limit: int = 300) -> Dict[str, Any]:
    rows = load_project_rows(path, filter_mode=filter_mode, start_date=start_date, end_date=end_date)
    out_rows = []
    today = date.today()
    due_30 = 0
    due_60 = 0
    due_90 = 0
    overdue = 0
    dates: List[date] = []
    locations = set()
    total_tests = 0

    for r in rows:
        if r.get("Location"):
            locations.add(str(r.get("Location")))
        td = _date_only(str(r.get("Test Date", "")))
        if td:
            dates.append(td)
        rd = _date_only(str(r.get("Retest Date", "")))
        if rd:
            days = (rd - today).days
            if days < 0:
                overdue += 1
            elif days <= 30:
                due_30 += 1
            elif days <= 60:
                due_60 += 1
            elif days <= 90:
                due_90 += 1
        total_tests += len(r.get("Tests", []) or [])

    for idx, r in enumerate(rows[:limit], start=1):
        tests = r.get("Tests", []) or []
        detail_tests = []
        for t in tests[:24]:
            detail_tests.append({
                "Measurement": t.get("Measurement", ""),
                "Category": t.get("Category", ""),
                "Status": t.get("Status", ""),
                "DateTime": t.get("DateTime", ""),
                "Reading": t.get("CompactResult", "") or t.get("Results", ""),
                "Limit": t.get("CompactLimit", ""),
                "Parameters": t.get("CompactParams", ""),
            })
        out_rows.append({
            "Row": idx,
            "Appliance ID": r.get("Appliance ID", ""),
            "Type": r.get("Type", ""),
            "Location": r.get("Location", ""),
            "Test Date": r.get("Test Date", ""),
            "Retest Date": r.get("Retest Date", ""),
            "Status": r.get("Status", ""),
            "User": r.get("User", ""),
            "Tests": len(tests),
            "Test Summary": _asset_test_summary(r),
            "Structure Path": r.get("Structure Path", ""),
            "Detail Tests": detail_tests,
        })

    pass_count = sum(1 for r in rows if str(r.get("Status", "")).lower() == "pass")
    fail_count = sum(1 for r in rows if str(r.get("Status", "")).lower() == "fail")
    recorded_count = len(rows) - pass_count - fail_count
    return {
        "rows": out_rows,
        "total_rows": len(rows),
        "total_tests": total_tests,
        "columns": ["Appliance ID", "Type", "Location", "Test Date", "Retest Date", "Status", "Tests", "Test Summary"],
        "source_type": _project_source_type(path),
        "stats": {
            "assets": len(rows),
            "passed": pass_count,
            "failed": fail_count,
            "recorded": recorded_count,
            "locations": len(locations),
            "due_30": due_30,
            "due_60": due_60,
            "due_90": due_90,
            "overdue": overdue,
            "date_start": min(dates).strftime("%d/%m/%Y") if dates else "",
            "date_end": max(dates).strftime("%d/%m/%Y") if dates else "",
            "preview_limit": limit,
        },
    }


@dataclass
class GeneralData:
    customer_no: str = ""
    inspection_record_no: str = ""
    order_no: str = ""
    customer_address: str = ""
    contractor: str = ""
    description: str = "Electrical equipment testing report"
    type_of_equipment: List[str] = field(default_factory=lambda: ["Portable appliance"])
    reason_for_test: List[str] = field(default_factory=lambda: ["Periodic test"])
    test_in_accordance_with: str = "AS/NZS 3760"
    start_of_testing: str = ""
    end_of_testing: str = ""
    instrument_model_1: str = "MI 3340"
    instrument_serial_1: str = "24460062"
    customer_contact_details: str = ""
    test_engineer_contact_details: str = ""
    attachments: List[str] = field(default_factory=lambda: ["Test results"])
    method_of_labelling: List[str] = field(default_factory=lambda: ["Pass/Fail tags"])
    date_of_next_inspection: str = ""
    results_summary: str = "No faults found"
    notes: str = ""
    operator_name: str = ""
    client_location: str = ""
    client_date: str = ""
    client_signature: str = ""
    operator_location: str = ""
    operator_date: str = ""
    operator_signature: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GeneralData":
        g = cls()
        for k, v in d.items():
            if hasattr(g, k):
                setattr(g, k, v)
        return g


def _checklist(options: Sequence[str], selected: Sequence[str], styles) -> str:
    return "   ".join(("☑" if o in selected else "☐") + " " + o for o in options)


def _cover_page(g: GeneralData, rows: List[Dict[str, Any]], styles) -> List[object]:
    w = PAGE_W - LEFT - RIGHT
    top = Table([["Customer No.:", g.customer_no, "Inspect. rec. No.:", g.inspection_record_no, "Order No.:", g.order_no]], colWidths=[25*mm, 28*mm, 30*mm, 28*mm, 24*mm, w-135*mm], rowHeights=[7*mm])
    top.setStyle(TableStyle([("GRID", (0,0), (-1,-1), .4, GRID), ("BACKGROUND", (1,0), (1,0), TTS_WHITE), ("BACKGROUND", (3,0), (3,0), TTS_WHITE), ("BACKGROUND", (5,0), (5,0), TTS_WHITE), ("FONT", (0,0), (-1,-1), FONT, 7)]))
    elems = [top, Spacer(1,2*mm), _p("PRO ELECTRICAL EQUIPMENT TEST REPORT", styles["TitleMain"]), _section("GENERAL DATA", styles)]
    data = [
        [_p("Customer address:", styles["Label"]), _p("Contractor:", styles["Label"])],
        [_p(g.customer_address, styles["Body"]), _p(g.contractor, styles["Body"])],
        [_p("Description:", styles["Label"]), _p(g.description, styles["Body"])],
    ]
    t = Table(data, colWidths=[w/2, w/2], rowHeights=[7*mm, 18*mm, 8*mm])
    t.setStyle(TableStyle([("GRID", (0,0), (-1,-1), .4, GRID), ("BACKGROUND", (0,0), (-1,0), LIGHT_GREY), ("SPAN", (1,2), (1,2)), ("VALIGN", (0,0), (-1,-1), "TOP")]))
    elems.append(t)
    equip = Table([[_p("Type of equipment:", styles["Label"]), _p(_checklist(["EV cable","Medical","Portable appliance","Machine","Welding","Switchgear","Other","CE Marketing"], g.type_of_equipment, styles), styles["BodySmall"]), _p("Reason for the test:", styles["Label"]), _p(_checklist(["In service","Repair","Periodic test","Other"], g.reason_for_test, styles), styles["BodySmall"])]], colWidths=[32*mm, 75*mm, 34*mm, w-141*mm], rowHeights=[18*mm])
    equip.setStyle(TableStyle([("GRID", (0,0), (-1,-1), .4, GRID), ("BACKGROUND", (0,0), (-1,-1), LIGHT_GREY), ("VALIGN", (0,0), (-1,-1), "TOP")]))
    elems.append(equip)
    dates = Table([[_p("Test in accordance with:", styles["Label"]), _p(g.test_in_accordance_with, styles["Body"]), _p("Start of testing:", styles["Label"]), _p(g.start_of_testing, styles["Body"]), _p("End of testing:", styles["Label"]), _p(g.end_of_testing, styles["Body"])]], colWidths=[36*mm, 42*mm, 28*mm, 30*mm, 26*mm, w-162*mm], rowHeights=[8*mm])
    dates.setStyle(TableStyle([("GRID", (0,0), (-1,-1), .4, GRID), ("BACKGROUND", (0,0), (-1,-1), LIGHT_GREY)]))
    elems.append(dates)
    inst = Table([[_p("Measuring instruments used:", styles["Label"]), _p("Model:", styles["Label"]), _p(g.instrument_model_1, styles["Body"]), _p("Serial No.:", styles["Label"]), _p(g.instrument_serial_1, styles["Body"])]], colWidths=[42*mm, 18*mm, 42*mm, 22*mm, w-124*mm], rowHeights=[8*mm])
    inst.setStyle(TableStyle([("GRID", (0,0), (-1,-1), .4, GRID), ("BACKGROUND", (0,0), (-1,-1), LIGHT_GREY)]))
    elems.append(inst)
    contact = Table([[_p("Customer contact details:", styles["Label"]), _p("Test engineer contact details:", styles["Label"])], [_p(g.customer_contact_details, styles["BodySmall"]), _p(g.test_engineer_contact_details, styles["BodySmall"])]], colWidths=[w/2, w/2], rowHeights=[6*mm, 14*mm])
    contact.setStyle(TableStyle([("GRID", (0,0), (-1,-1), .4, GRID), ("BACKGROUND", (0,0), (-1,0), LIGHT_GREY), ("VALIGN", (0,0), (-1,-1), "TOP")]))
    elems += [contact, Spacer(1,2*mm), _section("INSPECTION AND TEST RESULTS", styles)]
    statement = "All electrical equipment was tested in accordance with the listed regulations and technical standards. Equipment that passed was marked appropriately and can be declared safe according to accepted technical rules. Inspection and test results are summarized in the attached pages with a recommended re-test date."
    result = Table([[_p("Statement", styles["Label"]), _p("Date of next inspection:", styles["Label"])], [_p(statement, styles["BodySmall"]), _p(g.date_of_next_inspection, styles["Body"])], [_p("Method of labelling", styles["Label"]), _p(_checklist(["Pass/Fail tags","Barcoded tags","RFID tags"], g.method_of_labelling, styles), styles["BodySmall"])], [_p("Results:", styles["Label"]), _p(g.results_summary, styles["BodyBold"])], [_p("Notes:", styles["Label"]), _p(g.notes, styles["BodySmall"])]], colWidths=[w/2, w/2], rowHeights=[6*mm, 22*mm, 8*mm, 7*mm, 13*mm])
    result.setStyle(TableStyle([("GRID", (0,0), (-1,-1), .4, GRID), ("BACKGROUND", (0,0), (-1,0), LIGHT_GREY), ("BACKGROUND", (0,2), (0,4), LIGHT_GREY), ("VALIGN", (0,0), (-1,-1), "TOP")]))
    elems += [result, Spacer(1,2*mm), _section("SIGNATURE AND STAMP", styles)]
    sign = Table([[_p("Client:", styles["Label"]), _p("Operator:", styles["Label"])], [_p("☐ Report is fully accepted. Client is informed about inspection and test results.<br/>☐ Client is informed about status of faulty equipment.", styles["BodySmall"]), _p("☐ Electrical equipment was tested according to valid regulations and technical standards.<br/>☐ Faulty equipment and measures are appropriately noted.", styles["BodySmall"])], [_p(f"Location: {g.client_location}<br/>Date: {g.client_date}<br/>Signature: {g.client_signature}", styles["BodySmall"]), _p(f"Location: {g.operator_location}<br/>Date: {g.operator_date}<br/>Signature: {g.operator_signature or g.operator_name}", styles["BodySmall"])]], colWidths=[w/2, w/2], rowHeights=[6*mm, 17*mm, 14*mm])
    sign.setStyle(TableStyle([("GRID", (0,0), (-1,-1), .4, GRID), ("BACKGROUND", (0,0), (-1,0), LIGHT_GREY), ("VALIGN", (0,0), (-1,-1), "TOP")]))
    elems += [sign, PageBreak()]
    return elems


def _basic_title_page(g: GeneralData, rows: List[Dict[str, Any]], styles) -> List[object]:
    total = len(rows)
    passed = sum(1 for r in rows if str(r.get("Status", "")).lower() == "pass")
    failed = sum(1 for r in rows if str(r.get("Status", "")).lower() == "fail")
    title_style = ParagraphStyle("BasicTitle", parent=styles["TitleMain"], fontSize=24, leading=29, alignment=TA_CENTER)
    sub_style = ParagraphStyle("BasicSub", parent=styles["Body"], fontSize=11, leading=14, alignment=TA_CENTER)
    w = PAGE_W - LEFT - RIGHT
    elems = [Spacer(1, 24*mm), _p("TTS PRO REPORT", title_style), _p("Basic Electrical Equipment Test Report", sub_style), Spacer(1, 14*mm)]
    meta = Table([[_p("Source", styles["Label"]), _p("PADFX tester export", styles["Body"])], [_p("Description", styles["Label"]), _p(g.description, styles["Body"])], [_p("Generated", styles["Label"]), _p(datetime.now().strftime("%d/%m/%Y"), styles["Body"])]], colWidths=[38*mm, w-38*mm])
    meta.setStyle(TableStyle([("GRID", (0,0), (-1,-1), .45, GRID), ("BACKGROUND", (0,0), (0,-1), LIGHT_SEAFOAM), ("VALIGN", (0,0), (-1,-1), "TOP")]))
    stats = Table([[_p("Total", styles["Label"]), _p(str(total), styles["BodyBold"]), _p("Passed", styles["Label"]), _p(str(passed), styles["BodyBold"]), _p("Failed", styles["Label"]), _p(str(failed), styles["BodyBold"])]], colWidths=[24*mm, 22*mm, 24*mm, 22*mm, 24*mm, 22*mm])
    stats.setStyle(TableStyle([("GRID", (0,0), (-1,-1), .45, GRID), ("BACKGROUND", (0,0), (-1,-1), LIGHT_GREY), ("BACKGROUND", (3,0), (3,0), PASS_GREEN), ("BACKGROUND", (5,0), (5,0), FAIL_RED)]))
    elems += [meta, Spacer(1, 10*mm), stats, PageBreak()]
    return elems


def _summary_page(rows: List[Dict[str, Any]], styles) -> List[object]:
    elems = [_p("PRO REPORT - TEST REGISTER", styles["TitleMain"]), _section("RESULTS SUMMARY", styles)]
    headers = ["Appliance ID", "Type", "Location", "Test Date", "Retest Date", "Status", "Test Summary"]
    table_data = [[_p(h, styles["Label"]) for h in headers]]
    for r in rows:
        table_data.append([_p(r.get("Appliance ID", ""), styles["BodySmall"]), _p(r.get("Type", ""), styles["BodySmall"]), _p(r.get("Location", ""), styles["BodySmall"]), _p(r.get("Test Date", ""), styles["BodySmall"]), _p(r.get("Retest Date", ""), styles["BodySmall"]), _p(r.get("Status", ""), styles["BodySmall"]), _p(_asset_test_summary(r), styles["BodySmall"])])
    w = PAGE_W - LEFT - RIGHT
    col_widths = [22*mm, 30*mm, 25*mm, 22*mm, 23*mm, 16*mm, w - 138*mm]
    tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
    st = TableStyle([("GRID", (0,0), (-1,-1), .35, GRID), ("BACKGROUND", (0,0), (-1,0), LIGHT_SEAFOAM), ("VALIGN", (0,0), (-1,-1), "TOP"), ("LEFTPADDING", (0,0), (-1,-1), 2), ("RIGHTPADDING", (0,0), (-1,-1), 2), ("TOPPADDING", (0,0), (-1,-1), 2), ("BOTTOMPADDING", (0,0), (-1,-1), 2)])
    for i, r in enumerate(rows, start=1):
        if i % 2 == 0:
            st.add("BACKGROUND", (0,i), (-1,i), TTS_WHITE)
        if str(r.get("Status", "")).lower() == "pass":
            st.add("BACKGROUND", (5,i), (5,i), PASS_GREEN)
        elif str(r.get("Status", "")).lower() == "fail":
            st.add("BACKGROUND", (5,i), (5,i), FAIL_RED)
    tbl.setStyle(st)
    elems += [tbl, PageBreak()]
    return elems


def _asset_cards(rows: List[Dict[str, Any]], styles) -> List[object]:
    elems: List[object] = [_p("PRO ELECTRICAL EQUIPMENT TEST REPORT", styles["TitleMain"]), _section("APPLIANCE TEST DETAILS", styles)]
    w = PAGE_W - LEFT - RIGHT
    for idx, r in enumerate(rows):
        meta = Table([
            [_p("LOCATION:", styles["Label"]), _p(r.get("Location", ""), styles["BodySmall"]), _p("TEST DATE:", styles["Label"]), _p(r.get("Test Date", ""), styles["BodySmall"]), _p("SERIAL:", styles["Label"]), _p(r.get("Serial", ""), styles["BodySmall"])],
            [_p("TYPE:", styles["Label"]), _p(r.get("Type", ""), styles["BodySmall"]), _p("RETEST DATE:", styles["Label"]), _p(r.get("Retest Date", ""), styles["BodySmall"]), _p("USER:", styles["Label"]), _p(r.get("User", ""), styles["BodySmall"])],
            [_p("APPLIANCE ID:", styles["Label"]), _p(r.get("Appliance ID", ""), styles["BodySmall"]), _p("TEST SITE:", styles["Label"]), _p(r.get("Location", ""), styles["BodySmall"]), _p("STATUS:", styles["Label"]), _p(r.get("Status", ""), styles["BodySmall"])],
        ], colWidths=[24*mm, 36*mm, 27*mm, 34*mm, 20*mm, w-141*mm])
        meta.setStyle(TableStyle([("GRID", (0,0), (-1,-1), .35, GRID), ("BACKGROUND", (0,0), (-1,-1), LIGHT_SEAFOAM), ("BACKGROUND", (1,0), (1,-1), TTS_WHITE), ("BACKGROUND", (3,0), (3,-1), TTS_WHITE), ("BACKGROUND", (5,0), (5,-1), TTS_WHITE), ("VALIGN", (0,0), (-1,-1), "MIDDLE"), ("LEFTPADDING", (0,0), (-1,-1), 3)]))
        elems.append(KeepTogether([Spacer(1, 4*mm), meta]))
        tests = r.get("Tests", []) or []
        if tests:
            test_rows = [[_p("Test", styles["Label"]), _p("Reading / Result", styles["Label"]), _p("Limit", styles["Label"]), _p("Date / Time", styles["Label"]), _p("Status", styles["Label"])] ]
            for t in tests:
                test_name = t.get("Measurement", "Test")
                cat = t.get("Category", "")
                if cat:
                    test_name = f"{cat}<br/>{test_name}"
                test_rows.append([_p(test_name, styles["BodySmall"]), _p(t.get("CompactResult", "") or t.get("Results", ""), styles["BodySmall"]), _p(t.get("CompactLimit", ""), styles["BodySmall"]), _p(t.get("DateTime", ""), styles["BodySmall"]), _p(t.get("Status", ""), styles["BodySmall"])])
            compact = Table(test_rows, colWidths=[50*mm, 52*mm, 30*mm, 33*mm, w-165*mm], repeatRows=1)
            cstyle = TableStyle([("GRID", (0,0), (-1,-1), .3, GRID), ("BACKGROUND", (0,0), (-1,0), LIGHT_GREY), ("VALIGN", (0,0), (-1,-1), "TOP"), ("LEFTPADDING", (0,0), (-1,-1), 3), ("RIGHTPADDING", (0,0), (-1,-1), 3), ("TOPPADDING", (0,0), (-1,-1), 2), ("BOTTOMPADDING", (0,0), (-1,-1), 2)])
            for ridx, t in enumerate(tests, start=1):
                if ridx % 2 == 0:
                    cstyle.add("BACKGROUND", (0,ridx), (-1,ridx), TTS_WHITE)
                status = str(t.get("Status", "")).lower()
                if status == "pass":
                    cstyle.add("BACKGROUND", (4,ridx), (4,ridx), PASS_GREEN)
                elif status == "fail":
                    cstyle.add("BACKGROUND", (4,ridx), (4,ridx), FAIL_RED)
            compact.setStyle(cstyle)
            elems.append(Spacer(1,3*mm))
            elems.append(compact)
        if idx + 1 < len(rows):
            elems.append(PageBreak())
            elems += [_p("PRO ELECTRICAL EQUIPMENT TEST REPORT", styles["TitleMain"]), _section("APPLIANCE TEST DETAILS", styles)]
    return elems




def _retest_bucket(row: Dict[str, Any], today: Optional[date] = None) -> Tuple[str, Optional[int]]:
    today = today or date.today()
    rd = _date_only(str(row.get("Retest Date", "")))
    if not rd:
        return "No retest date", None
    days = (rd - today).days
    if days < 0:
        return "Overdue", days
    if days <= 30:
        return "Due in 30 days", days
    if days <= 60:
        return "Due in 60 days", days
    if days <= 90:
        return "Due in 90 days", days
    return "Later", days


def _retest_groups(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    groups = {"Overdue": [], "Due in 30 days": [], "Due in 60 days": [], "Due in 90 days": []}
    for r in rows:
        bucket, days = _retest_bucket(r)
        if bucket in groups:
            item = dict(r)
            item["Days"] = days
            groups[bucket].append(item)
    for key in groups:
        groups[key].sort(key=lambda x: (_date_only(str(x.get("Retest Date", ""))) or date.max, str(x.get("Location", "")), str(x.get("Appliance ID", ""))))
    return groups


def _upcoming_retest_report(g: GeneralData, rows: List[Dict[str, Any]], styles) -> List[object]:
    groups = _retest_groups(rows)
    w = PAGE_W - LEFT - RIGHT
    total_due = sum(len(v) for v in groups.values())
    title_style = ParagraphStyle("RetestTitle", parent=styles["TitleMain"], fontSize=22, leading=26, alignment=TA_CENTER)
    sub_style = ParagraphStyle("RetestSub", parent=styles["Body"], fontSize=10, leading=13, alignment=TA_CENTER)
    elems: List[object] = [
        Spacer(1, 12*mm),
        _p("TTS UPCOMING RETEST REPORT", title_style),
        _p("Assets due for retesting based on the latest PADFX test data", sub_style),
        Spacer(1, 8*mm),
    ]
    stats = Table([[
        _p("Overdue", styles["Label"]), _p(str(len(groups["Overdue"])), styles["BodyBold"]),
        _p("Due 30", styles["Label"]), _p(str(len(groups["Due in 30 days"])), styles["BodyBold"]),
        _p("Due 60", styles["Label"]), _p(str(len(groups["Due in 60 days"])), styles["BodyBold"]),
        _p("Due 90", styles["Label"]), _p(str(len(groups["Due in 90 days"])), styles["BodyBold"]),
        _p("Total", styles["Label"]), _p(str(total_due), styles["BodyBold"]),
    ]], colWidths=[18*mm, 14*mm, 18*mm, 14*mm, 18*mm, 14*mm, 18*mm, 14*mm, 18*mm, 14*mm])
    stats.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), .4, GRID),
        ("BACKGROUND", (0,0), (-1,-1), LIGHT_GREY),
        ("BACKGROUND", (1,0), (1,0), FAIL_RED),
        ("BACKGROUND", (3,0), (7,0), LIGHT_SEAFOAM),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ALIGN", (1,0), (-1,0), "CENTER"),
    ]))
    meta = Table([
        [_p("Customer / Site", styles["Label"]), _p(g.customer_address or "", styles["BodySmall"]), _p("Contractor", styles["Label"]), _p(g.contractor or "", styles["BodySmall"])],
        [_p("Generated", styles["Label"]), _p(datetime.now().strftime("%d/%m/%Y"), styles["BodySmall"]), _p("Report basis", styles["Label"]), _p("Latest test only - current compliance and retest status", styles["BodySmall"])],
    ], colWidths=[25*mm, (w-50*mm)/2, 25*mm, (w-50*mm)/2])
    meta.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), .35, GRID),
        ("BACKGROUND", (0,0), (-1,-1), TTS_WHITE),
        ("BACKGROUND", (0,0), (0,-1), LIGHT_SEAFOAM),
        ("BACKGROUND", (2,0), (2,-1), LIGHT_SEAFOAM),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 4),
        ("RIGHTPADDING", (0,0), (-1,-1), 4),
    ]))
    elems += [stats, Spacer(1, 5*mm), meta, Spacer(1, 6*mm)]

    headers = ["Asset ID", "Type", "Location", "Retest Date", "Days", "Status"]
    col_widths = [25*mm, 45*mm, 39*mm, 25*mm, 18*mm, 22*mm]
    for bucket in ["Overdue", "Due in 30 days", "Due in 60 days", "Due in 90 days"]:
        elems.append(_section(f"{bucket.upper()} ({len(groups[bucket])})", styles))
        data = [[_p(h, styles["Label"]) for h in headers]]
        if groups[bucket]:
            for r in groups[bucket]:
                days = r.get("Days")
                data.append([
                    _p(r.get("Appliance ID", ""), styles["BodySmall"]),
                    _p(r.get("Type", ""), styles["BodySmall"]),
                    _p(r.get("Location", ""), styles["BodySmall"]),
                    _p(r.get("Retest Date", ""), styles["BodySmall"]),
                    _p("" if days is None else str(days), styles["BodySmall"]),
                    _p(r.get("Status", ""), styles["BodySmall"]),
                ])
        else:
            data.append([_p("No assets in this bucket", styles["BodySmall"]), _p("", styles["BodySmall"]), _p("", styles["BodySmall"]), _p("", styles["BodySmall"]), _p("", styles["BodySmall"]), _p("", styles["BodySmall"])])
        tbl = Table(data, colWidths=col_widths, repeatRows=1)
        st = TableStyle([
            ("GRID", (0,0), (-1,-1), .3, GRID),
            ("BACKGROUND", (0,0), (-1,0), LIGHT_GREY),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("LEFTPADDING", (0,0), (-1,-1), 3),
            ("RIGHTPADDING", (0,0), (-1,-1), 3),
            ("TOPPADDING", (0,0), (-1,-1), 2),
            ("BOTTOMPADDING", (0,0), (-1,-1), 2),
        ])
        for i, r in enumerate(groups[bucket], start=1):
            if i % 2 == 0:
                st.add("BACKGROUND", (0,i), (-1,i), TTS_WHITE)
            if str(r.get("Status", "")).lower() == "fail":
                st.add("BACKGROUND", (5,i), (5,i), FAIL_RED)
            elif str(r.get("Status", "")).lower() == "pass":
                st.add("BACKGROUND", (5,i), (5,i), PASS_GREEN)
        tbl.setStyle(st)
        elems += [tbl, Spacer(1, 4*mm)]
    return elems

def generate_report(project_path: str | Path, output_pdf: str | Path, general_data: Optional[Dict[str, Any] | GeneralData] = None, include_asset_cards: bool = True, report_type: str = "pro", filter_mode: str = "latest", start_date: Optional[str] = None, end_date: Optional[str] = None) -> Path:
    rows = load_project_rows(project_path, filter_mode=filter_mode, start_date=start_date, end_date=end_date)
    g = general_data if isinstance(general_data, GeneralData) else GeneralData.from_dict(general_data or {})
    if not g.start_of_testing and rows:
        g.start_of_testing = rows[0].get("Test Date", "")
    if not g.end_of_testing and rows:
        g.end_of_testing = rows[-1].get("Test Date", "")
    if not g.date_of_next_inspection:
        vals = [r.get("Retest Date", "") for r in rows if r.get("Retest Date")]
        g.date_of_next_inspection = vals[0] if vals else ""
    output_pdf = Path(output_pdf)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()
    normalized_type = (report_type or "pro").lower()
    doc = BaseDocTemplate(str(output_pdf), pagesize=A4, leftMargin=LEFT, rightMargin=RIGHT, topMargin=TOP, bottomMargin=BOTTOM, title="TTS PRO Report", author="TTS PRO Report Generator")
    frame = Frame(LEFT, BOTTOM, PAGE_W-LEFT-RIGHT, PAGE_H-TOP-BOTTOM, id="normal")
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(FONT, 7)
        canvas.setStrokeColor(TTS_RED)
        canvas.line(LEFT, 12*mm, PAGE_W-RIGHT, 12*mm)
        canvas.drawString(LEFT, 8*mm, "TTS PRO Report")
        canvas.drawCentredString(PAGE_W/2, 8*mm, "Signature: ____________________     Customer: ____________________     Operator: ____________________")
        canvas.drawRightString(PAGE_W-RIGHT, 8*mm, f"{doc.page}")
        canvas.restoreState()
    doc.addPageTemplates([PageTemplate(id="All", frames=[frame], onPage=footer)])
    story: List[object] = []
    if normalized_type == "basic":
        story.extend(_basic_title_page(g, rows, styles))
        story.extend(_summary_page(rows, styles))
    elif normalized_type in {"retest", "upcoming_retest", "upcoming"}:
        # Upcoming retest reports should always be based on the current/latest asset state.
        if filter_mode != "latest":
            rows = load_padfx_rows(padfx_path, filter_mode="latest")
        story.extend(_upcoming_retest_report(g, rows, styles))
    else:
        story.extend(_cover_page(g, rows, styles))
        story.extend(_summary_page(rows, styles))
        if include_asset_cards:
            story.extend(_asset_cards(rows, styles))
    doc.build(story)
    return output_pdf
