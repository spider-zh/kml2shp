#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kivy Android UI for KML/KMZ <-> SHP conversion."""

import math
import os
import shutil
import traceback
from datetime import datetime

from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import LabelBase
from kivy.graphics import Color, Ellipse, Line, Rectangle
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform

import geo_core

IS_ANDROID = platform == "android"
APP_TITLE = "KML / SHP 转换器"


def _asset_path(name):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", name)


FONT_PATH = _asset_path("NotoSansCJKsc-Regular.otf")
FONT_NAME = "Roboto"
if os.path.exists(FONT_PATH):
    try:
        LabelBase.register(name="NotoCJK", fn_regular=FONT_PATH)
        FONT_NAME = "NotoCJK"
    except Exception:
        FONT_NAME = "Roboto"


def _hex_rgb(value):
    s = (value or "#2563eb").lstrip("#")
    if len(s) != 6:
        s = "2563eb"
    try:
        return int(s[0:2], 16) / 255.0, int(s[2:4], 16) / 255.0, int(s[4:6], 16) / 255.0
    except ValueError:
        return 0.15, 0.39, 0.92

def _android_cache_dir():
    try:
        from androidstorage4kivy.sharedstorage import SharedStorage
        return SharedStorage().get_cache_dir() or App.get_running_app().user_data_dir
    except Exception:
        return App.get_running_app().user_data_dir


def _pick_android_files(callback, multiple):
    from androidstorage4kivy.chooser import Chooser
    from androidstorage4kivy.sharedstorage import SharedStorage

    app = App.get_running_app()
    storage = SharedStorage()

    def on_result(uris):
        paths = []
        for uri in uris or []:
            try:
                path = storage.copy_from_shared(uri)
                if path:
                    paths.append(path)
            except Exception as exc:
                app.log("copy_from_shared failed: %s" % exc)
        Clock.schedule_once(lambda dt: callback(paths), 0)

    chooser = Chooser(on_result)
    app._android_chooser = chooser
    chooser.choose_content("*/*", multiple=multiple)


def pick_files(callback, multiple=False, filters=None):
    app = App.get_running_app()
    if IS_ANDROID:
        try:
            _pick_android_files(callback, multiple)
            return
        except Exception as exc:
            app.log("Native chooser unavailable: %s" % exc)
    start = os.path.expanduser("~")
    if IS_ANDROID:
        start = "/storage/emulated/0/Download"
        if not os.path.isdir(start):
            start = app.user_data_dir
    popup = FilePickerPopup(
        callback=callback,
        multiple=multiple,
        filters=filters,
        start_path=start,
    )
    app._file_popup = popup
    popup.open()

class FilePickerPopup(Popup):
    def __init__(self, callback, multiple=False, filters=None, start_path=".", **kwargs):
        super().__init__(title="选择文件", size_hint=(0.96, 0.96), **kwargs)
        self.callback = callback
        layout = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
        try:
            self.chooser = FileChooserListView(
                path=start_path,
                multiselect=multiple,
                filters=filters or ["*"],
                font_name=FONT_NAME,
            )
        except Exception:
            self.chooser = FileChooserListView(
                path=start_path,
                multiselect=multiple,
                filters=filters or ["*"],
            )
        layout.add_widget(self.chooser)
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        row.add_widget(Button(text="取消", on_release=lambda *_: self.dismiss()))
        row.add_widget(Button(text="打开", on_release=self._confirm))
        layout.add_widget(row)
        self.content = layout

    def _confirm(self, *_):
        selected = list(self.chooser.selection or [])
        if not selected and self.chooser.path:
            selected = [self.chooser.path]
        self.dismiss()
        if selected:
            Clock.schedule_once(lambda dt: self.callback(selected), 0)

class MapPreview(Widget):
    def __init__(self, on_select=None, **kwargs):
        super().__init__(**kwargs)
        self.layers = []
        self.selected = None
        self.on_select = on_select
        self.lat0 = 0.0
        self.cx = 0.0
        self.cy = 0.0
        self.scale = 100.0
        self._last_touch = None
        self._moved = False
        self.bind(size=self._schedule_redraw, pos=self._schedule_redraw)

    def _schedule_redraw(self, *_):
        Clock.schedule_once(lambda dt: self.redraw(), 0)

    def set_layers(self, layers):
        self.layers = layers
        self.selected = None
        self.fit()

    def set_selected(self, li, fi):
        self.selected = (li, fi)
        self.redraw()

    def _project(self, lon, lat):
        return lon * math.cos(math.radians(self.lat0)), lat

    def _local_xy(self, lon, lat):
        wx, wy = self._project(lon, lat)
        return (self.width / 2.0 + (wx - self.cx) * self.scale,
                self.height / 2.0 + (wy - self.cy) * self.scale)

    def _origin(self):
        try:
            return self.to_window(self.x, self.y)
        except Exception:
            return self.x, self.y

    def _screen_xy(self, lon, lat):
        lx, ly = self._local_xy(lon, lat)
        ox, oy = self._origin()
        return ox + lx, oy + ly

    def _touch_local(self, touch):
        ox, oy = self._origin()
        return touch.x - ox, touch.y - oy

    def fit(self):
        pts = []
        for layer in self.layers:
            if not layer.get("visible", True):
                continue
            for feature in layer.get("features", []):
                pts.extend(geo_core.iter_positions(feature.get("geometry")))
        if not pts:
            self.cx = 0.0
            self.cy = 0.0
            self.scale = 100.0
            self.redraw()
            return
        self.lat0 = sum(p[1] for p in pts) / len(pts)
        self.lat0 = max(-70.0, min(70.0, self.lat0))
        world = [self._project(p[0], p[1]) for p in pts]
        xs = [p[0] for p in world]
        ys = [p[1] for p in world]
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        self.cx = (xmin + xmax) / 2.0
        self.cy = (ymin + ymax) / 2.0
        bw = xmax - xmin
        bh = ymax - ymin
        if bw < 1e-8 and bh < 1e-8:
            self.scale = 100.0
        else:
            sx = (self.width - dp(20)) / bw if bw > 1e-8 else float("inf")
            sy = (self.height - dp(20)) / bh if bh > 1e-8 else float("inf")
            self.scale = max(1e-6, min(sx, sy))
        self.redraw()

    def zoom(self, factor):
        self.scale = max(1e-6, min(1e7, self.scale * factor))
        self.redraw()

    def redraw(self):
        if not self.get_parent_window():
            return
        self.canvas.clear()
        ox, oy = self._origin()
        with self.canvas:
            Color(0.97, 0.98, 0.99, 1)
            Rectangle(pos=(ox, oy), size=(self.width, self.height))
            self._draw_grid(ox, oy)
            for li, layer in enumerate(self.layers):
                if not layer.get("visible", True):
                    continue
                for fi, feature in enumerate(layer.get("features", [])):
                    selected = self.selected == (li, fi)
                    self._draw_feature(feature, selected)

    def _local_to_geo(self, lx, ly):
        wx = self.cx + (lx - self.width / 2.0) / self.scale
        wy = self.cy + (ly - self.height / 2.0) / self.scale
        c = max(abs(math.cos(math.radians(self.lat0))), 1e-9)
        return wx / c, wy

    def _draw_grid(self, ox, oy):
        gx0, gy0 = self._local_to_geo(0, 0)
        gx1, gy1 = self._local_to_geo(self.width, self.height)
        lon_min, lon_max = min(gx0, gx1), max(gx0, gx1)
        lat_min, lat_max = min(gy0, gy1), max(gy0, gy1)
        target = 90.0 / max(self.scale, 1e-9)
        try:
            mag = 10 ** math.floor(math.log10(max(target, 1e-12)))
        except Exception:
            mag = 1.0
        step = mag
        for mul in (1, 2, 5, 10):
            if mul * mag >= target:
                step = mul * mag
                break
        Color(0.86, 0.90, 0.94, 1)
        count = 0
        v = math.floor(lon_min / step) * step
        while v <= lon_max + step * 0.5 and count < 80:
            x, _ = self._screen_xy(v, 0)
            Line(points=[x, oy, x, oy + self.height], width=1)
            v += step
            count += 1
        count = 0
        v = math.floor(lat_min / step) * step
        while v <= lat_max + step * 0.5 and count < 80:
            _, y = self._screen_xy(0, v)
            Line(points=[ox, y, ox + self.width, y], width=1)
            v += step
            count += 1
        Color(0.75, 0.80, 0.86, 1)
        Line(rectangle=(ox, oy, self.width, self.height), width=1)

    def _draw_feature(self, feature, selected):
        geom = feature.get("geometry")
        if not geom:
            return
        rgb = _hex_rgb(feature.get("_color") or "#2563eb")
        if selected:
            Color(0.96, 0.55, 0.10, 1)
        else:
            Color(rgb[0], rgb[1], rgb[2], 0.95)
        self._draw_geometry(geom, 3 if selected else 1.5)

    def _draw_geometry(self, geom, width):
        t = geom.get("type")
        coords = geom.get("coordinates")
        if t == "Point":
            self._draw_point(coords)
        elif t == "MultiPoint":
            for p in coords:
                self._draw_point(p)
        elif t in ("LineString", "LinearRing"):
            self._draw_line(coords, width)
        elif t == "MultiLineString":
            for line in coords:
                self._draw_line(line, width)
        elif t == "Polygon":
            for i, ring in enumerate(coords):
                self._draw_line(ring, width, close=True)
        elif t == "MultiPolygon":
            for poly in coords:
                for ring in poly:
                    self._draw_line(ring, width, close=True)
        elif t == "GeometryCollection":
            for child in coords:
                self._draw_geometry(child, width)

    def _draw_point(self, p):
        x, y = self._screen_xy(p[0], p[1])
        Ellipse(pos=(x - dp(3), y - dp(3)), size=(dp(6), dp(6)))

    def _draw_line(self, points, width, close=False):
        if len(points) < 2:
            return
        flat = []
        for p in points:
            x, y = self._screen_xy(p[0], p[1])
            flat.extend([x, y])
        Line(points=flat, width=width, close=close)

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)
        self._last_touch = (touch.x, touch.y)
        self._moved = False
        return True

    def on_touch_move(self, touch):
        if self._last_touch is None:
            return super().on_touch_move(touch)
        dx = touch.x - self._last_touch[0]
        dy = touch.y - self._last_touch[1]
        if abs(dx) + abs(dy) > dp(3):
            self._moved = True
        self.cx -= dx / self.scale
        self.cy -= dy / self.scale
        self._last_touch = (touch.x, touch.y)
        self.redraw()
        return True

    def on_touch_up(self, touch):
        if self._last_touch is None:
            return super().on_touch_up(touch)
        self._last_touch = None
        if not self._moved:
            hit = self._hit_test(*self._touch_local(touch))
            if hit:
                self.selected = hit
                if self.on_select:
                    self.on_select(hit[0], hit[1])
                self.redraw()
            else:
                self.selected = None
                self.redraw()
        return True

    def _hit_test(self, lx, ly):
        best = None
        best_rank = 0
        ox, oy = self._origin()
        sx, sy = ox + lx, oy + ly
        for li, layer in enumerate(self.layers):
            if not layer.get("visible", True):
                continue
            for fi, feature in enumerate(layer.get("features", [])):
                rank = self._hit_rank(feature.get("geometry"), sx, sy)
                if rank >= best_rank and rank > 0:
                    best_rank = rank
                    best = (li, fi)
        return best

    def _hit_rank(self, geom, sx, sy):
        if not geom:
            return 0
        t = geom.get("type")
        coords = geom.get("coordinates")
        tol = dp(14)
        if t == "Point":
            x, y = self._screen_xy(coords[0], coords[1])
            return 3 if math.hypot(x - sx, y - sy) <= tol else 0
        if t == "MultiPoint":
            for p in coords:
                x, y = self._screen_xy(p[0], p[1])
                if math.hypot(x - sx, y - sy) <= tol:
                    return 3
            return 0
        if t in ("LineString", "LinearRing"):
            return 2 if self._hit_line(coords, sx, sy, tol) else 0
        if t == "MultiLineString":
            for line in coords:
                if self._hit_line(line, sx, sy, tol):
                    return 2
            return 0
        if t == "Polygon":
            return 1 if self._hit_polygon(coords, sx, sy, tol) else 0
        if t == "MultiPolygon":
            for poly in coords:
                if self._hit_polygon(poly, sx, sy, tol):
                    return 1
            return 0
        if t == "GeometryCollection":
            best = 0
            for child in coords:
                best = max(best, self._hit_rank(child, sx, sy))
            return best
        return 0

    def _hit_line(self, coords, sx, sy, tol):
        for i in range(len(coords) - 1):
            x1, y1 = self._screen_xy(coords[i][0], coords[i][1])
            x2, y2 = self._screen_xy(coords[i + 1][0], coords[i + 1][1])
            if _dist_point_segment(sx, sy, x1, y1, x2, y2) <= tol:
                return True
        return False

    def _hit_polygon(self, rings, sx, sy, tol):
        if not rings:
            return False
        screen_rings = []
        for ring in rings:
            screen = [self._screen_xy(p[0], p[1]) for p in ring]
            screen_rings.append(screen)
            for i in range(len(screen) - 1):
                if _dist_point_segment(sx, sy, screen[i][0], screen[i][1],
                                       screen[i + 1][0], screen[i + 1][1]) <= tol:
                    return True
        if not screen_rings or not _point_in_ring(sx, sy, screen_rings[0]):
            return False
        for hole in screen_rings[1:]:
            if _point_in_ring(sx, sy, hole):
                return False
        return True

def _dist_point_segment(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _point_in_ring(x, y, ring):
    if len(ring) < 3:
        return False
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside

class ConverterUI(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", spacing=dp(3), padding=dp(3), **kwargs)
        self.layers = []
        self.selected = None
        self._next_color = 0
        self._android_chooser = None
        self._file_popup = None
        self._build_ui()

    def _button(self, text, callback, **kwargs):
        kwargs.setdefault("font_name", FONT_NAME)
        kwargs.setdefault("font_size", sp(11))
        btn = Button(text=text, **kwargs)
        btn.bind(on_release=lambda *_: callback())
        return btn

    def _build_ui(self):
        top = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(4))
        title = Label(text=APP_TITLE, font_name=FONT_NAME, font_size=sp(17),
                      bold=True, color=(0.10, 0.16, 0.26, 1), halign="left")
        title.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        top.add_widget(title)
        self.status = Label(text="就绪", font_name=FONT_NAME, font_size=sp(10),
                            color=(0.30, 0.38, 0.50, 1), size_hint_x=0.9,
                            halign="right", valign="middle", shorten=True)
        self.status.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        top.add_widget(self.status)
        self.add_widget(top)

        actions = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(2))
        actions.add_widget(self._button("打开KML", self.open_kml, background_color=(0.15, 0.39, 0.92, 1)))
        actions.add_widget(self._button("打开SHP", self.open_shp, background_color=(0.05, 0.59, 0.41, 1)))
        actions.add_widget(self._button("导出KML", self.export_kml, background_color=(0.85, 0.47, 0.02, 1)))
        actions.add_widget(self._button("导出KMZ", self.export_kmz, background_color=(0.49, 0.23, 0.93, 1)))
        actions.add_widget(self._button("导出SHP", self.export_shp, background_color=(0.86, 0.15, 0.15, 1)))
        actions.add_widget(self._button("适应", self.fit_map))
        actions.add_widget(self._button("清空", self.clear_all))
        self.add_widget(actions)

        body = BoxLayout(orientation="horizontal", spacing=dp(3))
        self.add_widget(body)

        left = BoxLayout(orientation="vertical", size_hint_x=0.40, spacing=dp(2))
        left.add_widget(Label(text="图层 / 要素", font_name=FONT_NAME, font_size=sp(12),
                              bold=True, size_hint_y=None, height=dp(26),
                              color=(0.10, 0.16, 0.26, 1)))
        self.feature_scroll = ScrollView(do_scroll_x=False)
        self.feature_box = BoxLayout(orientation="vertical", size_hint_y=None,
                                     spacing=dp(2), padding=dp(2))
        self.feature_box.bind(minimum_height=self.feature_box.setter("height"))
        self.feature_scroll.add_widget(self.feature_box)
        left.add_widget(self.feature_scroll)

        left.add_widget(Label(text="属性", font_name=FONT_NAME, font_size=sp(12),
                              bold=True, size_hint_y=None, height=dp(26),
                              color=(0.10, 0.16, 0.26, 1)))
        self.attr_input = TextInput(
            text="点击地图或左侧要素查看属性", readonly=True,
            font_name=FONT_NAME, font_size=sp(11),
            background_color=(1, 1, 1, 1), foreground_color=(0.13, 0.17, 0.23, 1),
            cursor_color=(0.13, 0.17, 0.23, 1),
        )
        left.add_widget(self.attr_input)
        body.add_widget(left)

        right = BoxLayout(orientation="vertical", size_hint_x=0.60, spacing=dp(2))
        self.map_preview = MapPreview(on_select=self._on_map_select)
        right.add_widget(self.map_preview)

        map_actions = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(3))
        map_actions.add_widget(self._button("放大", lambda: self.map_preview.zoom(1.35)))
        map_actions.add_widget(self._button("缩小", lambda: self.map_preview.zoom(0.74)))
        map_actions.add_widget(self._button("适应视图", self.fit_map))
        right.add_widget(map_actions)
        body.add_widget(right)

    def set_status(self, text):
        self.status.text = text
        print(text)

    def open_kml(self):
        pick_files(self._on_kml_selected, multiple=False,
                   filters=["*.kml", "*.kmz", "*.KML", "*.KMZ"])

    def open_shp(self):
        pick_files(self._on_shp_selected, multiple=True,
                   filters=["*.shp", "*.dbf", "*.shx", "*.prj", "*.cpg",
                            "*.SHP", "*.DBF", "*.SHX", "*.PRJ", "*.CPG"])

    def _on_kml_selected(self, paths):
        if not paths:
            return
        loaded = 0
        for path in paths:
            if not path.lower().endswith((".kml", ".kmz")):
                continue
            try:
                layer = geo_core.load_kml_any(path)
                self._add_layer(layer)
                loaded += 1
                for warning in layer.get("warnings", []):
                    print("KML warning:", warning)
            except Exception as exc:
                self.set_status("打开失败: %s" % exc)
                print(traceback.format_exc())
        if loaded:
            self.set_status("已加载 %d 个 KML/KMZ 文件" % loaded)
            self._after_data_changed()

    def _on_shp_selected(self, paths):
        shp_paths = [p for p in paths if p.lower().endswith(".shp")]
        if not shp_paths:
            self.set_status("请选择 .shp 文件（建议同时选中同名 .dbf/.shx/.prj/.cpg）")
            return
        loaded = 0
        for path in shp_paths:
            try:
                layer = geo_core.read_shapefile(path)
                self._add_layer(layer)
                loaded += 1
                for warning in layer.get("warnings", []):
                    print("SHP warning:", warning)
            except Exception as exc:
                self.set_status("读取失败: %s" % exc)
                print(traceback.format_exc())
        if loaded:
            self.set_status("已加载 %d 个 Shapefile" % loaded)
            self._after_data_changed()

    def _add_layer(self, layer):
        colors = ["#2563eb", "#dc2626", "#059669", "#d97706",
                  "#7c3aed", "#0891b2", "#be185d", "#65a30d"]
        color = colors[self._next_color % len(colors)]
        self._next_color += 1
        layer.setdefault("color", color)
        layer.setdefault("visible", True)
        for feature in layer.get("features", []):
            if not feature.get("_color"):
                feature["_color"] = color
        self.layers.append(layer)

    def _after_data_changed(self):
        self._refresh_feature_list()
        self.map_preview.set_layers(self.layers)
        self.selected = None

    def _refresh_feature_list(self):
        self.feature_box.clear_widgets()
        if not self.layers:
            self.feature_box.add_widget(Label(text="暂无数据", font_name=FONT_NAME,
                                              font_size=sp(11), size_hint_y=None,
                                              height=dp(30)))
            return
        for li, layer in enumerate(self.layers):
            visible = layer.get("visible", True)
            rgb = _hex_rgb(layer.get("color"))
            btn = Button(
                text=("☑ " if visible else "☐ ") + str(layer.get("name", "图层")) +
                     "  (%d)" % len(layer.get("features", [])),
                font_name=FONT_NAME, font_size=sp(11),
                size_hint_y=None, height=dp(34), halign="left",
                background_normal="", background_color=(rgb[0], rgb[1], rgb[2], 0.28),
                color=(0.08, 0.12, 0.18, 1),
            )
            btn.bind(on_release=lambda *_, i=li: self.toggle_layer(i))
            self.feature_box.add_widget(btn)
            for fi, feature in enumerate(layer.get("features", [])[:100]):
                props = feature.get("properties") or {}
                name = props.get("name") or props.get("Name") or "要素 %d" % (fi + 1)
                label = "%s  [%s]" % (name, geo_core.geom_label(
                    (feature.get("geometry") or {}).get("type")))
                fbtn = Button(
                    text=label, font_name=FONT_NAME, font_size=sp(10),
                    size_hint_y=None, height=dp(30), halign="left",
                    background_normal="", background_color=(0.94, 0.96, 0.98, 1),
                    color=(0.20, 0.26, 0.34, 1),
                )
                fbtn.bind(on_release=lambda *_, i=li, j=fi: self.select_feature(i, j))
                self.feature_box.add_widget(fbtn)
            if len(layer.get("features", [])) > 100:
                self.feature_box.add_widget(Label(
                    text="（仅显示前 100 个要素）", font_name=FONT_NAME,
                    font_size=sp(10), size_hint_y=None, height=dp(24)))

    def select_feature(self, li, fi):
        if not (0 <= li < len(self.layers)):
            return
        features = self.layers[li].get("features", [])
        if not (0 <= fi < len(features)):
            return
        self.selected = (li, fi)
        self.map_preview.set_selected(li, fi)
        feature = features[fi]
        props = feature.get("properties") or {}
        geom = feature.get("geometry") or {}
        lines = [
            "图层: %s" % self.layers[li].get("name", ""),
            "几何: %s" % geo_core.geom_label(geom.get("type")),
            "坐标点数: %d" % sum(1 for _ in geo_core.iter_positions(geom)),
            "",
        ]
        for key, value in props.items():
            if key.startswith("_"):
                continue
            lines.append("%s = %s" % (key, geo_core.value_text(value)))
        self.attr_input.text = "\n".join(lines)
        self.set_status("已选择要素 %d" % (fi + 1))

    def _on_map_select(self, li, fi):
        self.select_feature(li, fi)

    def toggle_layer(self, li):
        if not (0 <= li < len(self.layers)):
            return
        self.layers[li]["visible"] = not self.layers[li].get("visible", True)
        self._refresh_feature_list()
        self.map_preview.redraw()
        self.set_status("图层 %s 已%s" % (
            self.layers[li].get("name", ""),
            "显示" if self.layers[li]["visible"] else "隐藏"))

    def fit_map(self):
        self.map_preview.fit()

    def clear_all(self):
        self.layers = []
        self.selected = None
        self.attr_input.text = "点击地图或左侧要素查看属性"
        self._refresh_feature_list()
        self.map_preview.set_layers([])
        self.set_status("已清空")

    def _has_features(self):
        return any(layer.get("features") for layer in self.layers)

    def _output_dir(self):
        return App.get_running_app().user_data_dir

    def _publish_outputs(self, paths):
        if not paths:
            return []
        if IS_ANDROID:
            try:
                from androidstorage4kivy.sharedstorage import SharedStorage
                storage = SharedStorage()
                result = []
                for path in paths:
                    name = os.path.basename(path)
                    uri = storage.copy_to_shared(
                        path, collection="Download",
                        filepath="KMLShpConverter/" + name,
                    )
                    result.append(str(uri) if uri else path)
                return result
            except Exception as exc:
                print("publish error:", exc)
                return paths
        dest_dir = os.path.join(os.path.expanduser("~"), "Downloads", "KMLShpConverter")
        try:
            os.makedirs(dest_dir, exist_ok=True)
            result = []
            for path in paths:
                dest = os.path.join(dest_dir, os.path.basename(path))
                shutil.copy2(path, dest)
                result.append(dest)
            return result
        except Exception as exc:
            print("copy output error:", exc)
            return paths

    def _finish_export(self, title, paths):
        shown = self._publish_outputs(paths)
        if IS_ANDROID:
            msg = "已导出到 下载/KMLShpConverter\n\n" + "\n".join(
                os.path.basename(p) for p in shown)
        else:
            msg = "已导出：\n" + "\n".join(shown)
        self.set_status("导出完成")
        self._show_message(title, msg)

    def export_kml(self):
        if not self._has_features():
            self.set_status("没有可导出的数据")
            return
        out = os.path.join(self._output_dir(),
                           "converted_%s.kml" % datetime.now().strftime("%Y%m%d_%H%M%S"))
        try:
            geo_core.write_kml(self.layers, out)
            self._finish_export("导出 KML", [out])
        except Exception as exc:
            self.set_status("导出失败: %s" % exc)
            print(traceback.format_exc())

    def export_kmz(self):
        if not self._has_features():
            self.set_status("没有可导出的数据")
            return
        out = os.path.join(self._output_dir(),
                           "converted_%s.kmz" % datetime.now().strftime("%Y%m%d_%H%M%S"))
        try:
            geo_core.write_kmz(self.layers, out)
            self._finish_export("导出 KMZ", [out])
        except Exception as exc:
            self.set_status("导出失败: %s" % exc)
            print(traceback.format_exc())

    def export_shp(self):
        if not self._has_features():
            self.set_status("没有可导出的数据")
            return
        all_features = []
        multi_layer = len(self.layers) > 1
        for layer in self.layers:
            for feature in layer.get("features", []):
                props = dict(feature.get("properties") or {})
                if multi_layer:
                    props.setdefault("layer", layer.get("name", "layer"))
                item = {"geometry": feature.get("geometry"), "properties": props}
                if feature.get("_color"):
                    item["_color"] = feature["_color"]
                all_features.append(item)
        groups = geo_core.partition_features(all_features)
        if not groups:
            self.set_status("没有可写入 Shapefile 的几何")
            return
        base = os.path.join(self._output_dir(),
                            "converted_%s" % datetime.now().strftime("%Y%m%d_%H%M%S"))
        suffixes = {"point": "_points", "line": "_lines", "polygon": "_polygons"}
        outputs = []
        warnings = []
        try:
            if len(groups) == 1:
                family, feats = next(iter(groups.items()))
                files, warns = geo_core.write_shapefile_set(base, feats)
                outputs.extend(files)
                warnings.extend(warns)
            else:
                for family in ("point", "line", "polygon"):
                    if family not in groups:
                        continue
                    files, warns = geo_core.write_shapefile_set(base + suffixes[family],
                                                               groups[family])
                    outputs.extend(files)
                    warnings.extend(warns)
            if warnings:
                print("SHP warnings:", warnings)
            self._finish_export("导出 SHP", outputs)
        except Exception as exc:
            self.set_status("导出失败: %s" % exc)
            print(traceback.format_exc())

    def _show_message(self, title, message):
        content = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
        label = Label(text=message, font_name=FONT_NAME, font_size=sp(11),
                      halign="left", valign="top")
        label.bind(size=lambda inst, val: setattr(inst, "text_size", val))
        content.add_widget(label)
        popup = Popup(title=title, content=content,
                      size_hint=(0.9, 0.55))
        content.add_widget(Button(text="确定", size_hint_y=None, height=dp(42),
                                  font_name=FONT_NAME,
                                  on_release=lambda *_: popup.dismiss()))
        popup.open()
        self._message_popup = popup


class KmlShpApp(App):
    def build(self):
        self.title = APP_TITLE
        return ConverterUI()

    def log(self, message):
        print(message)
        if self.root and hasattr(self.root, "set_status"):
            self.root.set_status(message)


if __name__ == "__main__":
    KmlShpApp().run()
