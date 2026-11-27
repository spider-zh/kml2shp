#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
geo_core.py
KML/KMZ <-> SHP 转换核心（纯标准库，无界面依赖）。
供 main.py（Kivy Android 界面）和桌面测试共同使用。
"""

from __future__ import annotations

import json
import math
import os
import re
import struct
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime

KML_NS = "http://www.opengis.net/kml/2.2"
WGS84_PRJ = (
    'GEOGCS["GCS_WGS_1984",'
    'DATUM["D_WGS_1984",'
    'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],'
    'UNIT["Degree",0.0174532925199433]]'
)

GEOMETRY_TAGS = {
    "Point", "LineString", "LinearRing", "Polygon",
    "MultiGeometry", "Track", "MultiTrack",
}

SHP_TYPE_NAMES = {
    0: "Null", 1: "Point", 3: "Polyline", 5: "Polygon", 8: "MultiPoint",
    11: "PointZ", 13: "PolylineZ", 15: "PolygonZ", 18: "MultiPointZ",
    21: "PointM", 23: "PolylineM", 25: "PolygonM", 28: "MultiPointM",
    31: "MultiPatch",
}


def _local_name(tag):
    return tag.rsplit("}", 1)[-1]


def _first_child(elem, name):
    for child in elem:
        if _local_name(child.tag) == name:
            return child
    return None


def _text_child(elem, name):
    child = _first_child(elem, name)
    if child is not None and child.text:
        return child.text.strip()
    return ""


def _ensure_closed(ring):
    pts = []
    for p in ring:
        if len(p) >= 3:
            pts.append((float(p[0]), float(p[1]), float(p[2])))
        else:
            pts.append((float(p[0]), float(p[1])))
    if not pts:
        return pts
    if pts[0][:2] != pts[-1][:2]:
        pts.append(pts[0])
    return pts


def _ring_area(ring):
    if len(ring) < 3:
        return 0.0
    s = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def _point_on_segment(px, py, x1, y1, x2, y2, eps=1e-10):
    cross = (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)
    if abs(cross) > eps:
        return False
    dot = (px - x1) * (px - x2) + (py - y1) * (py - y2)
    return dot <= eps


def _point_in_ring(x, y, ring):
    if len(ring) < 3:
        return False
    for i in range(len(ring)):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % len(ring)][0], ring[(i + 1) % len(ring)][1]
        if _point_on_segment(x, y, x1, y1, x2, y2):
            return True
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def _ring_representative(ring):
    if not ring:
        return 0.0, 0.0
    cx = sum(p[0] for p in ring) / len(ring)
    cy = sum(p[1] for p in ring) / len(ring)
    if _point_in_ring(cx, cy, ring):
        return cx, cy
    return ring[0][0], ring[0][1]


def _assemble_polygons(rings):
    clean = []
    for ring in rings:
        r = _ensure_closed(ring)
        if len(r) >= 4 and abs(_ring_area(r)) > 1e-15:
            clean.append(r)
    if not clean:
        return None
    if len(clean) == 1:
        r = clean[0]
        if _ring_area(r) < 0:
            r = list(reversed(r))
        return {"type": "Polygon", "coordinates": [r]}

    areas = [abs(_ring_area(r)) for r in clean]
    reps = [_ring_representative(r) for r in clean]
    n = len(clean)
    parents = [-1] * n
    for i in range(n):
        candidates = []
        for j in range(n):
            if i == j or areas[j] <= areas[i] + 1e-15:
                continue
            if _point_in_ring(reps[i][0], reps[i][1], clean[j]):
                candidates.append(j)
        if candidates:
            parents[i] = min(candidates, key=lambda k: areas[k])

    def depth(i):
        d = 0
        p = parents[i]
        while p != -1:
            d += 1
            p = parents[p]
        return d

    depths = [depth(i) for i in range(n)]
    outers = [i for i in range(n) if depths[i] % 2 == 0]
    holes = [i for i in range(n) if depths[i] % 2 == 1]
    hole_map = {i: [] for i in outers}
    for h in holes:
        p = parents[h]
        while p != -1 and depths[p] % 2 == 1:
            p = parents[p]
        if p == -1:
            possible = [o for o in outers if _point_in_ring(reps[h][0], reps[h][1], clean[o])]
            if possible:
                p = min(possible, key=lambda k: areas[k])
        if p != -1 and p in hole_map:
            hole_map[p].append(h)

    polygons = []
    for o in outers:
        outer = clean[o]
        if _ring_area(outer) < 0:
            outer = list(reversed(outer))
        poly = [outer]
        for h in hole_map.get(o, []):
            hr = clean[h]
            if _ring_area(hr) > 0:
                hr = list(reversed(hr))
            poly.append(hr)
        polygons.append(poly)
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def _combine_geometries(geoms):
    geoms = [g for g in geoms if g]
    if not geoms:
        return None
    if len(geoms) == 1:
        return geoms[0]
    types = {g["type"] for g in geoms}
    if types == {"Point"}:
        return {"type": "MultiPoint", "coordinates": [g["coordinates"] for g in geoms]}
    if types == {"LineString"}:
        return {"type": "MultiLineString", "coordinates": [g["coordinates"] for g in geoms]}
    if types == {"Polygon"}:
        return {"type": "MultiPolygon", "coordinates": [g["coordinates"] for g in geoms]}
    return {"type": "GeometryCollection", "coordinates": geoms}


def iter_positions(geom):
    if not geom:
        return
    t = geom.get("type")
    coords = geom.get("coordinates")
    if t == "Point":
        yield coords
    elif t == "MultiPoint":
        for p in coords:
            yield p
    elif t == "LineString":
        for p in coords:
            yield p
    elif t == "MultiLineString":
        for line in coords:
            for p in line:
                yield p
    elif t == "Polygon":
        for ring in coords:
            for p in ring:
                yield p
    elif t == "MultiPolygon":
        for poly in coords:
            for ring in poly:
                for p in ring:
                    yield p
    elif t == "GeometryCollection":
        for g in coords:
            yield from iter_positions(g)


def geom_family(geom):
    t = (geom or {}).get("type")
    if t in ("Point", "MultiPoint"):
        return "point"
    if t in ("LineString", "MultiLineString"):
        return "line"
    if t in ("Polygon", "MultiPolygon"):
        return "polygon"
    return None


def explode_geometry(geom):
    if not geom:
        return
    if geom.get("type") == "GeometryCollection":
        for g in geom.get("coordinates", []):
            yield from explode_geometry(g)
    else:
        yield geom


def geom_label(geom_type):
    return {
        "Point": "点", "MultiPoint": "多点",
        "LineString": "线", "MultiLineString": "多线",
        "Polygon": "面", "MultiPolygon": "多面",
        "GeometryCollection": "几何集合",
    }.get(geom_type, "未知")


def _bbox_of_positions(positions):
    pts = list(positions)
    if not pts:
        raise ValueError("没有有效坐标")
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def value_text(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format(value, ".15g")
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


# ------------------------------ KML 读取 ------------------------------

def _parse_coords(text):
    if not text:
        return []
    result = []
    for token in text.replace("\n", " ").replace("\t", " ").split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            vals = [float(x) for x in parts if x != ""]
            if len(vals) >= 3:
                result.append((vals[0], vals[1], vals[2]))
            elif len(vals) >= 2:
                result.append((vals[0], vals[1]))
        except ValueError:
            continue
    return result


def _kml_color_to_hex(text):
    if not text:
        return None
    s = text.strip().lower()
    if len(s) == 8:
        return "#" + s[6:8] + s[4:6] + s[2:4]
    if len(s) == 6:
        return "#" + s
    return None


def _style_color(style):
    if style is None:
        return None
    for tag in ("PolyStyle", "LineStyle", "IconStyle"):
        child = _first_child(style, tag)
        if child is None:
            continue
        color_el = _first_child(child, "color")
        if color_el is not None:
            c = _kml_color_to_hex(color_el.text)
            if c:
                return c
    return None


def _collect_styles(root):
    styles = {}
    for elem in root.iter():
        if _local_name(elem.tag) == "Style":
            sid = elem.get("id")
            if sid:
                styles[sid] = _style_color(elem)
    for elem in root.iter():
        if _local_name(elem.tag) != "StyleMap":
            continue
        sid = elem.get("id")
        if not sid:
            continue
        for child in elem.iter():
            if _local_name(child.tag) == "styleUrl" and child.text:
                ref = child.text.strip()
                if ref.startswith("#"):
                    styles[sid] = styles.get(ref[1:])
                    break
    return styles


def _parse_geometry(node):
    if node is None:
        return None
    tag = _local_name(node.tag)

    if tag == "Point":
        for el in node.iter():
            if _local_name(el.tag) == "coordinates":
                coords = _parse_coords(el.text)
                if coords:
                    return {"type": "Point", "coordinates": coords[0]}
        return None

    if tag in ("LineString", "LinearRing"):
        for el in node.iter():
            if _local_name(el.tag) == "coordinates":
                coords = _parse_coords(el.text)
                if len(coords) >= 2:
                    return {"type": "LineString", "coordinates": coords}
        return None

    if tag == "Polygon":
        outer = None
        holes = []
        for child in node:
            ctag = _local_name(child.tag)
            if ctag not in ("outerBoundaryIs", "innerBoundaryIs"):
                continue
            ring_coords = []
            for el in child.iter():
                if _local_name(el.tag) == "coordinates":
                    ring_coords = _parse_coords(el.text)
                    break
            if not ring_coords:
                continue
            ring_coords = _ensure_closed(ring_coords)
            if len(ring_coords) < 4:
                continue
            if ctag == "outerBoundaryIs":
                outer = ring_coords
            else:
                holes.append(ring_coords)
        if outer is None:
            return None
        return {"type": "Polygon", "coordinates": [outer] + holes}

    if tag == "MultiGeometry":
        geoms = []
        for child in node:
            if _local_name(child.tag) in GEOMETRY_TAGS:
                geoms.append(_parse_geometry(child))
        return _combine_geometries(geoms)

    if tag == "Track":
        coords = []
        for el in node.iter():
            if _local_name(el.tag) != "coord" or not el.text:
                continue
            parts = el.text.split()
            try:
                if len(parts) >= 3:
                    coords.append((float(parts[0]), float(parts[1]), float(parts[2])))
                elif len(parts) >= 2:
                    coords.append((float(parts[0]), float(parts[1])))
            except ValueError:
                continue
        if len(coords) >= 2:
            return {"type": "LineString", "coordinates": coords}
        if coords:
            return {"type": "Point", "coordinates": coords[0]}
        return None

    if tag == "MultiTrack":
        geoms = []
        for child in node:
            if _local_name(child.tag) == "Track":
                geoms.append(_parse_geometry(child))
        return _combine_geometries(geoms)
    return None


def _parse_placemark(pm, styles):
    props = {}
    name = _text_child(pm, "name")
    if name:
        props["name"] = name
    desc = _text_child(pm, "description")
    if desc:
        props["description"] = desc

    ext = _first_child(pm, "ExtendedData")
    if ext is not None:
        for el in ext.iter():
            tag = _local_name(el.tag)
            if tag == "Data":
                key = el.get("name")
                val_el = _first_child(el, "value")
                val = val_el.text.strip() if val_el is not None and val_el.text else ""
                if key:
                    props[key] = val
            elif tag == "SimpleData":
                key = el.get("name")
                val = el.text.strip() if el.text else ""
                if key:
                    props[key] = val

    for child in pm:
        ctag = _local_name(child.tag)
        if ctag == "TimeStamp":
            when = _text_child(child, "when")
            if when:
                props["timestamp"] = when
        elif ctag == "TimeSpan":
            begin = _text_child(child, "begin")
            end = _text_child(child, "end")
            if begin:
                props["time_begin"] = begin
            if end:
                props["time_end"] = end

    color = None
    style = _first_child(pm, "Style")
    if style is not None:
        color = _style_color(style)
    if not color:
        style_url = _text_child(pm, "styleUrl")
        if style_url.startswith("#"):
            color = styles.get(style_url[1:])

    geoms = []
    for child in pm:
        if _local_name(child.tag) in GEOMETRY_TAGS:
            geoms.append(_parse_geometry(child))
    geom = _combine_geometries(geoms)
    if geom is None:
        return None
    feature = {"geometry": geom, "properties": props}
    if color:
        feature["_color"] = color
    return feature


def parse_kml_bytes(data, layer_name="KML"):
    root = ET.fromstring(data)
    styles = _collect_styles(root)
    features = []

    def walk(elem):
        tag = _local_name(elem.tag)
        if tag == "Placemark":
            feat = _parse_placemark(elem, styles)
            if feat:
                features.append(feat)
            return
        for child in elem:
            walk(child)

    walk(root)
    if not features:
        raise ValueError("KML 中未找到可读取的 Placemark 几何。")
    return {
        "name": layer_name, "features": features,
        "source": "", "visible": True, "warnings": [],
    }


def load_kml_any(path):
    base = os.path.splitext(os.path.basename(path))[0]
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path, "r") as zf:
            names = [n for n in zf.namelist()
                     if n.lower().endswith(".kml") and "__MACOSX" not in n]
            if not names:
                raise ValueError("KMZ/ZIP 中没有找到 .kml 文件。")
            names.sort(key=lambda n: (os.path.basename(n).lower() != "doc.kml", -len(n)))
            data = zf.read(names[0])
    else:
        with open(path, "rb") as f:
            data = f.read()
    layer = parse_kml_bytes(data, base)
    layer["source"] = path
    return layer

# ------------------------------ KML 写出 ------------------------------

def _q(tag):
    return "{%s}%s" % (KML_NS, tag)


def _fmt_number(v):
    try:
        return format(float(v), ".10f").rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "0"


def _positions_text(points):
    parts = []
    for p in points:
        if len(p) >= 3:
            parts.append("%s,%s,%s" % (_fmt_number(p[0]), _fmt_number(p[1]), _fmt_number(p[2])))
        else:
            parts.append("%s,%s" % (_fmt_number(p[0]), _fmt_number(p[1])))
    return " ".join(parts)


def _hex_to_kml_color(hex_color, alpha="ff"):
    if not hex_color:
        hex_color = "#2563eb"
    s = hex_color.lstrip("#")
    if len(s) != 6:
        s = "2563eb"
    return alpha + s[4:6] + s[2:4] + s[0:2]


def _write_ring(parent, boundary_tag, ring):
    boundary = ET.SubElement(parent, _q(boundary_tag))
    linear = ET.SubElement(boundary, _q("LinearRing"))
    ET.SubElement(linear, _q("coordinates")).text = _positions_text(_ensure_closed(ring))


def _append_polygon(parent, polygon_coords):
    poly = ET.SubElement(parent, _q("Polygon"))
    for i, ring in enumerate(polygon_coords):
        _write_ring(poly, "outerBoundaryIs" if i == 0 else "innerBoundaryIs", ring)


def _append_linestring(parent, coords):
    line = ET.SubElement(parent, _q("LineString"))
    ET.SubElement(line, _q("tessellate")).text = "1"
    ET.SubElement(line, _q("coordinates")).text = _positions_text(coords)


def _xml_geom(parent, geom):
    if not geom:
        return
    t = geom.get("type")
    coords = geom.get("coordinates")
    if t == "Point":
        p = ET.SubElement(parent, _q("Point"))
        ET.SubElement(p, _q("coordinates")).text = _positions_text([coords])
    elif t == "MultiPoint":
        mg = ET.SubElement(parent, _q("MultiGeometry"))
        for point in coords:
            p = ET.SubElement(mg, _q("Point"))
            ET.SubElement(p, _q("coordinates")).text = _positions_text([point])
    elif t == "LineString":
        _append_linestring(parent, coords)
    elif t == "MultiLineString":
        mg = ET.SubElement(parent, _q("MultiGeometry"))
        for line in coords:
            _append_linestring(mg, line)
    elif t == "Polygon":
        _append_polygon(parent, coords)
    elif t == "MultiPolygon":
        mg = ET.SubElement(parent, _q("MultiGeometry"))
        for polygon in coords:
            _append_polygon(mg, polygon)
    elif t == "GeometryCollection":
        mg = ET.SubElement(parent, _q("MultiGeometry"))
        for child in coords:
            _xml_geom(mg, child)


def _write_placemark(folder, feature, index):
    props = feature.get("properties", {}) or {}
    pm = ET.SubElement(folder, _q("Placemark"))
    name = props.get("name") or props.get("Name") or "要素 %d" % index
    ET.SubElement(pm, _q("name")).text = str(name)
    if props.get("description"):
        ET.SubElement(pm, _q("description")).text = value_text(props["description"])

    color = feature.get("_color") or "#2563eb"
    style = ET.SubElement(pm, _q("Style"))
    line_style = ET.SubElement(style, _q("LineStyle"))
    ET.SubElement(line_style, _q("color")).text = _hex_to_kml_color(color, "ff")
    ET.SubElement(line_style, _q("width")).text = "2"
    poly_style = ET.SubElement(style, _q("PolyStyle"))
    ET.SubElement(poly_style, _q("color")).text = _hex_to_kml_color(color, "66")
    icon_style = ET.SubElement(style, _q("IconStyle"))
    ET.SubElement(icon_style, _q("color")).text = _hex_to_kml_color(color, "ff")
    ET.SubElement(icon_style, _q("scale")).text = "0.8"

    ext = ET.SubElement(pm, _q("ExtendedData"))
    for key, value in props.items():
        if key in ("name", "Name", "description") or key.startswith("_"):
            continue
        data = ET.SubElement(ext, _q("Data"))
        data.set("name", str(key))
        ET.SubElement(data, _q("value")).text = value_text(value)
    _xml_geom(pm, feature.get("geometry"))


def _build_kml_tree(layers, title):
    ET.register_namespace("", KML_NS)
    root = ET.Element(_q("kml"))
    doc = ET.SubElement(root, _q("Document"))
    ET.SubElement(doc, _q("name")).text = title
    total = 0
    for layer in layers:
        features = layer.get("features", [])
        if not features:
            continue
        folder = ET.SubElement(doc, _q("Folder"))
        ET.SubElement(folder, _q("name")).text = str(layer.get("name", "图层"))
        for i, feature in enumerate(features, 1):
            _write_placemark(folder, feature, i)
            total += 1
    if total == 0:
        raise ValueError("没有可导出的要素。")
    try:
        ET.indent(root, space="  ")
    except AttributeError:
        pass
    return root


def write_kml(layers, path):
    title = os.path.splitext(os.path.basename(path))[0]
    root = _build_kml_tree(layers, title)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_kmz(layers, path):
    title = os.path.splitext(os.path.basename(path))[0]
    root = _build_kml_tree(layers, title)
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("doc.kml", data)


# ------------------------------ DBF 读取 ------------------------------

def _decode_bytes(raw, preferred="utf-8"):
    if raw is None:
        return ""
    candidates = [preferred, "utf-8", "gbk", "big5", "cp1252", "latin1"]
    seen = set()
    for enc in candidates:
        if not enc:
            continue
        key = enc.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin1", errors="replace")


def _detect_dbf_encoding(cpg_path, lang_driver):
    if cpg_path and os.path.exists(cpg_path):
        try:
            with open(cpg_path, "r", encoding="ascii", errors="ignore") as f:
                label = f.read().strip()
            upper = label.upper()
            if "UTF" in upper:
                return "utf-8"
            if "936" in upper or "GBK" in upper or "GB2312" in upper:
                return "gbk"
            if "950" in upper or "BIG5" in upper:
                return "big5"
            if label:
                return label
        except OSError:
            pass
    if lang_driver == 0x4D:
        return "gbk"
    return "utf-8"


def _parse_dbf_value(raw, ftype, decimals, encoding):
    if raw is None:
        return ""
    ftype = (ftype or "C").upper()
    if ftype in ("C", "M"):
        raw = raw.split(b"\x00", 1)[0].strip(b" \x00")
        return _decode_bytes(raw, encoding)
    text = raw.decode("ascii", errors="ignore").strip().strip("\x00")
    if not text:
        return ""
    if ftype in ("N", "F"):
        try:
            if ftype == "F" or decimals > 0 or "." in text or "e" in text.lower():
                value = float(text)
                return int(value) if value.is_integer() and ftype == "N" else value
            return int(text)
        except ValueError:
            return text
    if ftype == "D":
        digits = re.sub(r"\D", "", text)
        if len(digits) == 8:
            return "%s-%s-%s" % (digits[0:4], digits[4:6], digits[6:8])
        return text
    if ftype == "L":
        c = text.upper()[:1]
        if c in ("T", "Y"):
            return True
        if c in ("F", "N"):
            return False
        return None
    if ftype == "B":
        try:
            return float(text)
        except ValueError:
            return text
    return _decode_bytes(raw, encoding).strip().strip("\x00")


def read_dbf(path, cpg_path=None):
    if not path or not os.path.exists(path):
        return [], [], "utf-8", []
    with open(path, "rb") as f:
        data = f.read()
    warnings = []
    if len(data) < 32:
        return [], [], "utf-8", ["DBF 文件过短，已忽略属性。"]
    lang_driver = data[29]
    encoding = _detect_dbf_encoding(cpg_path, lang_driver)
    try:
        num_records = struct.unpack_from("<I", data, 4)[0]
        header_len = struct.unpack_from("<H", data, 8)[0]
        record_len = struct.unpack_from("<H", data, 10)[0]
    except struct.error:
        return [], [], encoding, ["DBF 文件头损坏，已忽略属性。"]

    fields = []
    offset = 32
    field_index = 0
    while offset + 32 <= header_len - 1:
        if data[offset] == 0x0D:
            break
        name_raw = data[offset:offset + 11].split(b"\x00", 1)[0]
        name = _decode_bytes(name_raw, encoding).strip() or "FIELD_%d" % (field_index + 1)
        fields.append({
            "name": name,
            "type": chr(data[offset + 11]),
            "length": data[offset + 16],
            "decimals": data[offset + 17],
        })
        field_index += 1
        offset += 32

    used = set()
    for field in fields:
        original = field["name"]
        name = original
        n = 2
        while name in used:
            name = "%s_%d" % (original, n)
            n += 1
        field["name"] = name
        used.add(name)

    records = []
    for i in range(num_records):
        start = header_len + i * record_len
        if start + record_len > len(data):
            warnings.append("DBF 记录数超过文件长度，部分记录被忽略。")
            break
        if data[start:start + 1] == b"*":
            records.append(None)
            continue
        pos = start + 1
        values = {}
        for field in fields:
            raw = data[pos:pos + field["length"]]
            pos += field["length"]
            values[field["name"]] = _parse_dbf_value(raw, field["type"], field["decimals"], encoding)
        records.append(values)
    return fields, records, encoding, warnings

# --- KML writer ---


def _q(tag):
    return "{%s}%s" % (KML_NS, tag)

def _fmt_number(v):
    try:
        return format(float(v), ".10f").rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "0"

def _positions_text(points):
    parts = []
    for p in points:
        if len(p) >= 3:
            parts.append("%s,%s,%s" % (_fmt_number(p[0]), _fmt_number(p[1]), _fmt_number(p[2])))
        else:
            parts.append("%s,%s" % (_fmt_number(p[0]), _fmt_number(p[1])))
    return " ".join(parts)

def _hex_to_kml_color(hex_color, alpha="ff"):
    if not hex_color:
        hex_color = "#2563eb"
    s = hex_color.lstrip("#")
    if len(s) != 6:
        s = "2563eb"
    return alpha + s[4:6] + s[2:4] + s[0:2]

def _write_ring(parent, boundary_tag, ring):
    boundary = ET.SubElement(parent, _q(boundary_tag))
    linear = ET.SubElement(boundary, _q("LinearRing"))
    ET.SubElement(linear, _q("coordinates")).text = _positions_text(_ensure_closed(ring))

def _append_polygon(parent, polygon_coords):
    poly = ET.SubElement(parent, _q("Polygon"))
    for i, ring in enumerate(polygon_coords):
        _write_ring(poly, "outerBoundaryIs" if i == 0 else "innerBoundaryIs", ring)

def _append_linestring(parent, coords):
    line = ET.SubElement(parent, _q("LineString"))
    ET.SubElement(line, _q("tessellate")).text = "1"
    ET.SubElement(line, _q("coordinates")).text = _positions_text(coords)

def _xml_geom(parent, geom):
    if not geom:
        return
    t = geom.get("type")
    coords = geom.get("coordinates")
    if t == "Point":
        p = ET.SubElement(parent, _q("Point"))
        ET.SubElement(p, _q("coordinates")).text = _positions_text([coords])
    elif t == "MultiPoint":
        mg = ET.SubElement(parent, _q("MultiGeometry"))
        for point in coords:
            p = ET.SubElement(mg, _q("Point"))
            ET.SubElement(p, _q("coordinates")).text = _positions_text([point])
    elif t == "LineString":
        _append_linestring(parent, coords)
    elif t == "MultiLineString":
        mg = ET.SubElement(parent, _q("MultiGeometry"))
        for line in coords:
            _append_linestring(mg, line)
    elif t == "Polygon":
        _append_polygon(parent, coords)
    elif t == "MultiPolygon":
        mg = ET.SubElement(parent, _q("MultiGeometry"))
        for polygon in coords:
            _append_polygon(mg, polygon)
    elif t == "GeometryCollection":
        mg = ET.SubElement(parent, _q("MultiGeometry"))
        for child in coords:
            _xml_geom(mg, child)

def _write_placemark(folder, feature, index):
    props = feature.get("properties", {}) or {}
    pm = ET.SubElement(folder, _q("Placemark"))
    name = props.get("name") or props.get("Name") or "要素 %d" % index
    ET.SubElement(pm, _q("name")).text = str(name)
    if props.get("description"):
        ET.SubElement(pm, _q("description")).text = value_text(props["description"])
    color = feature.get("_color") or "#2563eb"
    style = ET.SubElement(pm, _q("Style"))
    line_style = ET.SubElement(style, _q("LineStyle"))
    ET.SubElement(line_style, _q("color")).text = _hex_to_kml_color(color, "ff")
    ET.SubElement(line_style, _q("width")).text = "2"
    poly_style = ET.SubElement(style, _q("PolyStyle"))
    ET.SubElement(poly_style, _q("color")).text = _hex_to_kml_color(color, "66")
    icon_style = ET.SubElement(style, _q("IconStyle"))
    ET.SubElement(icon_style, _q("color")).text = _hex_to_kml_color(color, "ff")
    ET.SubElement(icon_style, _q("scale")).text = "0.8"
    ext = ET.SubElement(pm, _q("ExtendedData"))
    for key, value in props.items():
        if key in ("name", "Name", "description") or key.startswith("_"):
            continue
        data = ET.SubElement(ext, _q("Data"))
        data.set("name", str(key))
        ET.SubElement(data, _q("value")).text = value_text(value)
    _xml_geom(pm, feature.get("geometry"))

def _build_kml_tree(layers, title):
    ET.register_namespace("", KML_NS)
    root = ET.Element(_q("kml"))
    doc = ET.SubElement(root, _q("Document"))
    ET.SubElement(doc, _q("name")).text = title
    total = 0
    for layer in layers:
        features = layer.get("features", [])
        if not features:
            continue
        folder = ET.SubElement(doc, _q("Folder"))
        ET.SubElement(folder, _q("name")).text = str(layer.get("name", "图层"))
        for i, feature in enumerate(features, 1):
            _write_placemark(folder, feature, i)
            total += 1
    if total == 0:
        raise ValueError("没有可导出的要素。")
    try:
        ET.indent(root, space="  ")
    except AttributeError:
        pass
    return root

def write_kml(layers, path):
    title = os.path.splitext(os.path.basename(path))[0]
    root = _build_kml_tree(layers, title)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)

def write_kmz(layers, path):
    title = os.path.splitext(os.path.basename(path))[0]
    root = _build_kml_tree(layers, title)
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("doc.kml", data)

def _decode_bytes(raw, preferred="utf-8"):
    if raw is None:
        return ""
    seen = set()
    for enc in [preferred, "utf-8", "gbk", "big5", "cp1252", "latin1"]:
        if not enc or enc.lower() in seen:
            continue
        seen.add(enc.lower())
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin1", errors="replace")

def _detect_dbf_encoding(cpg_path, lang_driver):
    if cpg_path and os.path.exists(cpg_path):
        try:
            with open(cpg_path, "r", encoding="ascii", errors="ignore") as f:
                label = f.read().strip()
            upper = label.upper()
            if "UTF" in upper:
                return "utf-8"
            if "936" in upper or "GBK" in upper or "GB2312" in upper:
                return "gbk"
            if "950" in upper or "BIG5" in upper:
                return "big5"
            if label:
                return label
        except OSError:
            pass
    if lang_driver == 0x4D:
        return "gbk"
    return "utf-8"

def _parse_dbf_value(raw, ftype, decimals, encoding):
    if raw is None:
        return ""
    ftype = (ftype or "C").upper()
    if ftype in ("C", "M"):
        raw = raw.split(b"\x00", 1)[0].strip(b" \x00")
        return _decode_bytes(raw, encoding)
    text = raw.decode("ascii", errors="ignore").strip().strip("\x00")
    if not text:
        return ""
    if ftype in ("N", "F"):
        try:
            if ftype == "F" or decimals > 0 or "." in text or "e" in text.lower():
                value = float(text)
                return int(value) if value.is_integer() and ftype == "N" else value
            return int(text)
        except ValueError:
            return text
    if ftype == "D":
        digits = re.sub(r"\D", "", text)
        if len(digits) == 8:
            return "%s-%s-%s" % (digits[0:4], digits[4:6], digits[6:8])
        return text
    if ftype == "L":
        c = text.upper()[:1]
        if c in ("T", "Y"):
            return True
        if c in ("F", "N"):
            return False
        return None
    if ftype == "B":
        try:
            return float(text)
        except ValueError:
            return text
    return _decode_bytes(raw, encoding).strip().strip("\x00")

def read_dbf(path, cpg_path=None):
    if not path or not os.path.exists(path):
        return [], [], "utf-8", []
    with open(path, "rb") as f:
        data = f.read()
    warnings = []
    if len(data) < 32:
        return [], [], "utf-8", ["DBF 文件过短，已忽略属性。"]
    encoding = _detect_dbf_encoding(cpg_path, data[29])
    try:
        num_records = struct.unpack_from("<I", data, 4)[0]
        header_len = struct.unpack_from("<H", data, 8)[0]
        record_len = struct.unpack_from("<H", data, 10)[0]
    except struct.error:
        return [], [], encoding, ["DBF 文件头损坏，已忽略属性。"]
    fields = []
    offset = 32
    field_index = 0
    while offset + 32 <= header_len - 1:
        if data[offset] == 0x0D:
            break
        name_raw = data[offset:offset + 11].split(b"\x00", 1)[0]
        name = _decode_bytes(name_raw, encoding).strip() or "FIELD_%d" % (field_index + 1)
        fields.append({"name": name, "type": chr(data[offset + 11]),
                       "length": data[offset + 16], "decimals": data[offset + 17]})
        field_index += 1
        offset += 32
    used = set()
    for field in fields:
        original = field["name"]
        name = original
        n = 2
        while name in used:
            name = "%s_%d" % (original, n)
            n += 1
        field["name"] = name
        used.add(name)
    records = []
    for i in range(num_records):
        start = header_len + i * record_len
        if start + record_len > len(data):
            warnings.append("DBF 记录数超过文件长度，部分记录被忽略。")
            break
        if data[start:start + 1] == b"*":
            records.append(None)
            continue
        pos = start + 1
        values = {}
        for field in fields:
            raw = data[pos:pos + field["length"]]
            pos += field["length"]
            values[field["name"]] = _parse_dbf_value(raw, field["type"], field["decimals"], encoding)
        records.append(values)
    return fields, records, encoding, warnings

def _find_companion(shp_path, ext):
    folder = os.path.dirname(os.path.abspath(shp_path))
    stem = os.path.splitext(os.path.basename(shp_path))[0].lower()
    wanted = stem + ext.lower()
    try:
        for name in os.listdir(folder):
            if name.lower() == wanted:
                return os.path.join(folder, name)
    except OSError:
        pass
    return None

def _parse_shape_record(content):
    if len(content) < 4:
        return None, "Shape 记录过短"
    stype = struct.unpack_from("<i", content, 0)[0]
    if stype == 0:
        return None, None
    try:
        if stype in (1, 21):
            x, y = struct.unpack_from("<dd", content, 4)
            return {"type": "Point", "coordinates": (x, y)}, None
        if stype == 11:
            x, y, z = struct.unpack_from("<ddd", content, 4)
            return {"type": "Point", "coordinates": (x, y, z)}, None
        if stype in (8, 18, 28):
            n = struct.unpack_from("<i", content, 36)[0]
            if n <= 0:
                return None, None
            pts_off = 40
            points = []
            for i in range(n):
                x, y = struct.unpack_from("<dd", content, pts_off + 16 * i)
                points.append((x, y))
            if stype == 18:
                z_off = pts_off + 16 * n + 16
                zs = struct.unpack_from("<%dd" % n, content, z_off)
                points = [(points[i][0], points[i][1], zs[i]) for i in range(n)]
            return {"type": "MultiPoint", "coordinates": points}, None
        if stype in (3, 13, 23, 5, 15, 25):
            n_parts = struct.unpack_from("<i", content, 36)[0]
            n_points = struct.unpack_from("<i", content, 40)[0]
            if n_parts <= 0 or n_points <= 0:
                return None, None
            parts = list(struct.unpack_from("<%di" % n_parts, content, 44))
            points_off = 44 + 4 * n_parts
            points = []
            for i in range(n_points):
                x, y = struct.unpack_from("<dd", content, points_off + 16 * i)
                points.append((x, y))
            if stype in (13, 15):
                z_off = points_off + 16 * n_points + 16
                zs = struct.unpack_from("<%dd" % n_points, content, z_off)
                points = [(points[i][0], points[i][1], zs[i]) for i in range(n_points)]
            parts.append(n_points)
            lines = []
            for k in range(n_parts):
                a, b = parts[k], parts[k + 1]
                if a < 0 or b > n_points or b - a < 2:
                    continue
                lines.append(points[a:b])
            if not lines:
                return None, None
            if stype in (3, 13, 23):
                if len(lines) == 1:
                    return {"type": "LineString", "coordinates": lines[0]}, None
                return {"type": "MultiLineString", "coordinates": lines}, None
            return _assemble_polygons(lines), None
        return None, "暂不支持 Shape 类型 %d" % stype
    except (struct.error, IndexError, ValueError) as exc:
        return None, "Shape 记录解析失败：%s" % exc

def _is_wgs84_prj(text):
    t = (text or "").upper().replace(" ", "")
    return "WGS_1984" in t or "WGS84" in t or "4326" in t

def read_shapefile(shp_path):
    if not shp_path.lower().endswith(".shp"):
        raise ValueError("请选择 .shp 文件。")
    if not os.path.exists(shp_path):
        raise FileNotFoundError(shp_path)
    base = os.path.splitext(os.path.basename(shp_path))[0]
    dbf_path = _find_companion(shp_path, ".dbf")
    prj_path = _find_companion(shp_path, ".prj")
    cpg_path = _find_companion(shp_path, ".cpg")
    warnings = []
    if not dbf_path:
        warnings.append("未找到同名 .dbf 文件，属性表将为空。")
    if not prj_path:
        warnings.append("未找到 .prj，坐标按 WGS84 经纬度解释。")
    else:
        try:
            with open(prj_path, "r", encoding="utf-8", errors="ignore") as f:
                if not _is_wgs84_prj(f.read()):
                    warnings.append("检测到非 WGS84 坐标系；本工具不做投影转换。")
        except OSError:
            warnings.append("无法读取 .prj 文件。")
    _fields, dbf_records, encoding, dbf_warnings = read_dbf(dbf_path, cpg_path)
    warnings.extend(dbf_warnings)
    with open(shp_path, "rb") as f:
        data = f.read()
    if len(data) < 100:
        raise ValueError("SHP 文件过短或损坏。")
    if struct.unpack_from(">i", data, 0)[0] != 9994:
        warnings.append("SHP 文件头标识异常，仍尝试读取。")
    shape_type = struct.unpack_from("<i", data, 32)[0]
    if shape_type == 31:
        warnings.append("MultiPatch 类型暂不支持几何解析。")
    features = []
    pos = 100
    record_index = 0
    while pos + 8 <= len(data):
        try:
            _record_no, content_words = struct.unpack_from(">ii", data, pos)
        except struct.error:
            break
        if content_words < 0:
            warnings.append("遇到非法记录长度，停止读取。")
            break
        start = pos + 8
        end = start + content_words * 2
        if end > len(data):
            warnings.append("SHP 记录被截断，停止读取。")
            break
        geom, record_warning = _parse_shape_record(data[start:end])
        if record_warning and record_warning not in warnings:
            warnings.append(record_warning)
        if geom is not None:
            deleted = record_index < len(dbf_records) and dbf_records[record_index] is None
            if not deleted:
                props = {}
                if record_index < len(dbf_records) and dbf_records[record_index]:
                    props = dict(dbf_records[record_index])
                features.append({"geometry": geom, "properties": props})
        record_index += 1
        pos = end
    if not features:
        raise ValueError("SHP 中没有读取到有效几何。")
    if dbf_records and len(dbf_records) != record_index:
        warnings.append("DBF 与 SHP 记录数不一致。")
    return {"name": base, "features": features, "source": shp_path,
            "visible": True, "warnings": warnings,
            "shape_type": SHP_TYPE_NAMES.get(shape_type, str(shape_type)),
            "encoding": encoding}

def _position_z(p):
    return float(p[2]) if len(p) >= 3 else 0.0


def _truncate_utf8(text, max_bytes):
    raw = str(text).encode("utf-8")[:max_bytes]
    while raw and (raw[-1] & 0xC0) == 0x80:
        raw = raw[:-1]
    return raw.decode("utf-8", errors="ignore")

def _make_unique_field_names(keys):
    result = []
    used = set()
    for index, key in enumerate(keys):
        base = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in str(key).strip())
        if not base:
            base = "FIELD_%d" % (index + 1)
        if base[0].isdigit():
            base = "F_" + base
        base = _truncate_utf8(base, 10)
        candidate = base
        n = 2
        while candidate.lower() in used:
            suffix = "_%d" % n
            candidate = _truncate_utf8(base, max(1, 10 - len(suffix))) + suffix
            n += 1
        used.add(candidate.lower())
        result.append(candidate)
    return result

def _encode_fixed(text, length):
    raw = str(text).replace("\x00", " ").encode("utf-8")
    if len(raw) > length:
        raw = raw[:length]
        while raw and (raw[-1] & 0xC0) == 0x80:
            raw = raw[:-1]
        raw = raw.decode("utf-8", errors="ignore").encode("utf-8")
    return raw + b" " * (length - len(raw))

def _write_dbf(path, rows):
    warnings = []
    keys = []
    seen = set()
    for row in rows:
        for key in row:
            if key.startswith("_"):
                continue
            if key not in seen:
                seen.add(key)
                keys.append(key)
    names = _make_unique_field_names(keys)
    fields = []
    for key, name in zip(keys, names):
        max_len = 1
        truncated = False
        for row in rows:
            byte_len = len(value_text(row.get(key)).encode("utf-8"))
            if byte_len > 254:
                truncated = True
            max_len = max(max_len, min(byte_len, 254))
        if truncated:
            warnings.append("Field %s value too long, truncated." % key)
        fields.append({"key": key, "name": name, "length": max_len})

    header_len = 32 + 32 * len(fields) + 1
    record_len = 1 + sum(f["length"] for f in fields)
    if record_len > 65535:
        raise ValueError("DBF record too long")
    now = datetime.now()
    data = bytearray(32)
    data[0] = 0x03
    data[1] = max(0, now.year - 1900) & 0xFF
    data[2] = now.month
    data[3] = now.day
    struct.pack_into("<I", data, 4, len(rows))
    struct.pack_into("<H", data, 8, header_len)
    struct.pack_into("<H", data, 10, record_len)
    for field in fields:
        desc = bytearray(32)
        name_raw = field["name"].encode("utf-8")[:11]
        desc[0:len(name_raw)] = name_raw
        desc[11] = ord("C")
        desc[16] = field["length"]
        data.extend(desc)
    data.append(0x0D)
    for row in rows:
        data.append(0x20)
        for field in fields:
            data.extend(_encode_fixed(value_text(row.get(field["key"])), field["length"]))
    data.append(0x1A)
    with open(path, "wb") as f:
        f.write(data)
    return warnings

def _shape_content(geom, is_z):
    family = geom_family(geom)
    if family == "point":
        if geom["type"] == "Point":
            points = [geom["coordinates"]]
            stype = 11 if is_z else 1
            multi = False
        else:
            points = list(geom["coordinates"])
            stype = 18 if is_z else 8
            multi = True
        if not points:
            raise ValueError("empty point")
        xmin, ymin, xmax, ymax = _bbox_of_positions(points)
        if not multi:
            x, y = points[0][0], points[0][1]
            if is_z:
                content = struct.pack("<i", stype)
                content += struct.pack("<dddd", x, y, _position_z(points[0]), 0.0)
                return content, points
            return struct.pack("<i", stype) + struct.pack("<dd", x, y), points
        content = struct.pack("<i", stype)
        content += struct.pack("<4d", xmin, ymin, xmax, ymax)
        content += struct.pack("<i", len(points))
        content += b"".join(struct.pack("<dd", p[0], p[1]) for p in points)
        if is_z:
            zs = [_position_z(p) for p in points]
            content += struct.pack("<dd", min(zs), max(zs))
            content += struct.pack("<%dd" % len(zs), *zs)
            content += struct.pack("<dd", 0.0, 0.0)
            content += struct.pack("<%dd" % len(zs), *([0.0] * len(zs)))
        return content, points

    if family == "line":
        if geom["type"] == "LineString":
            lines = [list(geom["coordinates"])]
        else:
            lines = [list(line) for line in geom["coordinates"]]
        lines = [line for line in lines if len(line) >= 2]
        if not lines:
            raise ValueError("empty line")
        stype = 13 if is_z else 3
        points = [p for line in lines for p in line]
        bbox = _bbox_of_positions(points)
        parts = []
        index = 0
        for line in lines:
            parts.append(index)
            index += len(line)
        content = struct.pack("<i", stype)
        content += struct.pack("<4d", *bbox)
        content += struct.pack("<ii", len(lines), len(points))
        content += struct.pack("<%di" % len(parts), *parts)
        content += b"".join(struct.pack("<dd", p[0], p[1]) for p in points)
        if is_z:
            zs = [_position_z(p) for p in points]
            content += struct.pack("<dd", min(zs), max(zs))
            content += struct.pack("<%dd" % len(zs), *zs)
            content += struct.pack("<dd", 0.0, 0.0)
            content += struct.pack("<%dd" % len(zs), *([0.0] * len(zs)))
        return content, points

    if family == "polygon":
        if geom["type"] == "Polygon":
            polygons = [geom["coordinates"]]
        else:
            polygons = geom["coordinates"]
        rings = []
        for polygon in polygons:
            for i, ring in enumerate(polygon):
                r = _ensure_closed(ring)
                if len(r) < 4:
                    continue
                area = _ring_area(r)
                if i == 0:
                    if area > 0:
                        r = list(reversed(r))
                else:
                    if area < 0:
                        r = list(reversed(r))
                rings.append(r)
        if not rings:
            raise ValueError("empty polygon")
        stype = 15 if is_z else 5
        points = [p for ring in rings for p in ring]
        bbox = _bbox_of_positions(points)
        parts = []
        index = 0
        for ring in rings:
            parts.append(index)
            index += len(ring)
        content = struct.pack("<i", stype)
        content += struct.pack("<4d", *bbox)
        content += struct.pack("<ii", len(rings), len(points))
        content += struct.pack("<%di" % len(parts), *parts)
        content += b"".join(struct.pack("<dd", p[0], p[1]) for p in points)
        if is_z:
            zs = [_position_z(p) for p in points]
            content += struct.pack("<dd", min(zs), max(zs))
            content += struct.pack("<%dd" % len(zs), *zs)
            content += struct.pack("<dd", 0.0, 0.0)
            content += struct.pack("<%dd" % len(zs), *([0.0] * len(zs)))
        return content, points
    raise ValueError("geometry cannot be written to shapefile")

def _make_shp_header(file_length_bytes, shape_type, bbox, zrange=None):
    header = bytearray(100)
    struct.pack_into(">i", header, 0, 9994)
    struct.pack_into(">i", header, 24, file_length_bytes // 2)
    struct.pack_into("<i", header, 28, 1000)
    struct.pack_into("<i", header, 32, shape_type)
    struct.pack_into("<4d", header, 36, bbox[0], bbox[1], bbox[2], bbox[3])
    if zrange is not None:
        struct.pack_into("<4d", header, 68, zrange[0], zrange[1], 0.0, 0.0)
    return bytes(header)

def _write_shp_files(base, shape_type, contents, bbox, zrange=None):
    shp_body = bytearray()
    shx_body = bytearray()
    for index, content in enumerate(contents, 1):
        if len(content) % 2 != 0:
            content += b"\x00"
        offset_words = (100 + len(shp_body)) // 2
        content_words = len(content) // 2
        shp_body.extend(struct.pack(">ii", index, content_words))
        shp_body.extend(content)
        shx_body.extend(struct.pack(">ii", offset_words, content_words))
    with open(base + ".shp", "wb") as f:
        f.write(_make_shp_header(100 + len(shp_body), shape_type, bbox, zrange))
        f.write(shp_body)
    with open(base + ".shx", "wb") as f:
        f.write(_make_shp_header(100 + len(shx_body), shape_type, bbox, zrange))
        f.write(shx_body)

def write_shapefile_set(base_path, features):
    if not features:
        raise ValueError("no features")
    base = os.path.splitext(base_path)[0]
    warnings = []
    preview = []
    for feature in features:
        preview.extend(iter_positions(feature.get("geometry")))
    if not preview:
        raise ValueError("no coordinates")
    is_z = any(len(p) >= 3 for p in preview)
    if is_z and any(len(p) < 3 for p in preview):
        warnings.append("Some coordinates have no Z value; using 0.")
    contents, rows, all_positions = [], [], []
    for index, feature in enumerate(features, 1):
        geom = feature.get("geometry")
        if not geom:
            continue
        try:
            content, positions = _shape_content(geom, is_z)
        except ValueError as exc:
            warnings.append("Feature %d skipped: %s" % (index, exc))
            continue
        contents.append(content)
        all_positions.extend(positions)
        rows.append({k: v for k, v in (feature.get("properties") or {}).items()
                     if not k.startswith("_")})
    if not contents:
        raise ValueError("no writable geometry")
    bbox = _bbox_of_positions(all_positions)
    zrange = None
    if is_z:
        zs = [_position_z(p) for p in all_positions]
        zrange = (min(zs), max(zs))
    shape_type = struct.unpack_from("<i", contents[0], 0)[0]
    _write_shp_files(base, shape_type, contents, bbox, zrange)
    warnings.extend(_write_dbf(base + ".dbf", rows))
    with open(base + ".prj", "w", encoding="ascii") as f:
        f.write(WGS84_PRJ)
    with open(base + ".cpg", "w", encoding="ascii") as f:
        f.write("UTF-8")
    return [base + ".shp", base + ".shx", base + ".dbf",
            base + ".prj", base + ".cpg"], warnings

def partition_features(features):
    groups = {}
    for feature in features:
        geom = feature.get("geometry")
        if not geom:
            continue
        for part in explode_geometry(geom):
            family = geom_family(part)
            if not family:
                continue
            new_feature = {"geometry": part,
                           "properties": dict(feature.get("properties") or {})}
            if feature.get("_color"):
                new_feature["_color"] = feature["_color"]
            groups.setdefault(family, []).append(new_feature)
    return groups
