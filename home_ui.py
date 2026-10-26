"""Homepage scan cards and CPlus scanning core animation (stdlib Tk)."""
from __future__ import annotations

import math
from pathlib import Path
import time
import localized_tk as tk
from i18n import tr
from tkinter import font

import home_scan
import home_core
from desktop import WHITE, INK, MUTED, MINT, CORAL, FONT, PillButton, rounded, size_text, disk_name, disk_snapshot
from ui_widgets import check_images, MetricLabel


DESCRIPTIONS = dict(cdrive="释放磁盘空间", chat="整理多款聊天软件空间",
                    large="大文件分区迁移", privacy="清理使用痕迹", compress="软件智能压缩",
                    popup="自动拦截软件弹窗", migrate="软件智能迁移", uninstall="管理已安装的软件")
PREFIXES = dict(cdrive="可清理", chat="可清理", large="可搬家",
                privacy="可清理", compress="可压缩", popup="可拦截", migrate="可搬家", uninstall="可卸载")


class HomeHeadline(MetricLabel):
    def __init__(self, parent, home, **kwargs):
        self.home = home
        super().__init__(parent, **kwargs)

    def _paint(self):
        super()._paint()
        self.home.backdrop(self, self.winfo_x(), self.winfo_y(), tag="heading-background")
        self.tag_lower("heading-background")
        if self.home.header_icon and hasattr(self, "_caption"):
            width = max(self.winfo_width(), self.winfo_reqwidth())
            start = (width-self._measure(tr(self._caption)))/2
            self.create_image(start-21, max(14, self.winfo_height()/2), image=self.home.header_icon)


class HomeButton(PillButton):
    def __init__(self, parent, home, text, command, **kwargs):
        self.home = home
        super().__init__(parent, text, command, **kwargs)

    def paint(self, hover=False):
        super().paint(hover)
        self.home.backdrop(self, self.winfo_x(), self.winfo_y(), tag="button-background")
        self.tag_lower("button-background")


class HomeLink(tk.Canvas):
    def __init__(self, parent, home, command):
        super().__init__(parent, width=130, height=28, highlightthickness=0, bg=WHITE,
                         cursor="hand2", takefocus=True)
        self.home, self.command, self.caption, self.enabled = home, command, "↻ 扫描", True
        self.bind("<Button-1>", lambda event: self.invoke())
        self.bind("<Return>", lambda event: self.invoke())
        self.bind("<space>", lambda event: self.invoke())

    def paint(self):
        self.delete("all")
        self.home.backdrop(self, self.winfo_x(), self.winfo_y())
        self.create_text(65, 14, text=self.caption, fill=INK if self.enabled else MUTED, font=(FONT, 10))

    def invoke(self):
        if self.enabled:
            self.command()


class HomeDisk(tk.Canvas):
    def __init__(self, parent, home):
        super().__init__(parent, height=55, bg=WHITE, highlightthickness=0)
        self.home, self.info = home, None

    def update_info(self, info):
        self.info = info
        self.paint()

    def paint(self):
        self.delete("all")
        self.home.backdrop(self, self.winfo_x(), self.winfo_y())
        width = max(100, self.winfo_width())
        if not self.info:
            self.create_text(0, 11, anchor="w", text="磁盘空间暂时无法读取", fill=MUTED, font=(FONT, 9))
            return
        data = self.info
        self.create_text(0, 11, anchor="w", text=f"{disk_name(data)}空间", fill="#6c8787",
                         font=(FONT, 9), tags="disk-title")
        self.create_text(width, 11, anchor="e",
                         text=f"可用 {size_text(data['free'])}  /  共 {size_text(data['total'])}",
                         fill=MUTED, font=(FONT, 9), tags="disk-amount")
        rounded(self, 0, 27, width, 35, 4, fill="#e5e8e7", outline="")
        if data["total"] and data["used"]:
            rounded(self, 0, 27, max(7, width*min(1, data["used"]/data["total"])), 35,
                    4, fill=CORAL, outline="")


class HomeTile(tk.Canvas):
    def __init__(self, parent, home, key):
        super().__init__(parent, bg="#f6fcfc", highlightthickness=0, cursor="hand2", takefocus=True)
        self.home, self.key, self.selected = home, key, True
        self.state, self.value, self.completed, self.total = "idle", 0, None, None
        self.message = ""
        self.bind("<Button-1>", self.click)
        self.bind("<space>", lambda event: self.toggle())
        self.bind("<Return>", lambda event: home.open_feature(key))
        self.bind("<Configure>", lambda event: self.paint())

    def toggle(self):
        if not self.home.scanning:
            self.selected = not self.selected
            self.home.update_heading()
            self.paint()

    def click(self, event):
        if event.x > self.winfo_width()-24 and event.y < 24:
            self.toggle()
        else:
            self.home.open_feature(self.key)

    def paint(self):
        self.delete("all")
        w, h = max(100, self.winfo_width()), max(50, self.winfo_height())
        rounded(self, 1, 2, w-1, h-1, 4, fill="#f6fcfc", outline="#b9e8e1")
        self.create_text(13, 22, anchor="w", text=home_scan.LABELS[self.key], fill="#303030",
                         font=(FONT, 9, "bold"), tags="title")
        if not self.home.scanning:
            self.create_image(w-10, 10, image=self.home.checks["on" if self.selected else "off"], tags="check")
        number, detail_color = None, "#999999"
        if self.state == "done" and self.message.startswith("未找到"):
            detail = "未找到目录"
            caption = "选择目录 ›"
        elif self.state in ("done", "cancelled"):
            detail = PREFIXES[self.key]
            number = size_text(self.value) if home_scan.UNITS[self.key] == "bytes" else f"{self.value}{home_scan.UNITS[self.key]}"
            caption = "部分结果 ›" if self.state == "cancelled" else "前往处理 ›"
        elif self.state == "failed":
            detail, detail_color = "扫描未完成", CORAL
            caption = "前往 ›" if self.key in {"migrate", "compress", "chat"} else "重试 ›"
        else:
            detail = "正在读取信息" if self.state == "running" else DESCRIPTIONS[self.key]
            caption = "扫描中" if self.state == "running" else "等待扫描" if self.state == "waiting" else "扫描 ›"
            if self.state == "idle" and self.key in {"migrate", "compress", "chat"}:
                caption = "前往 ›"
        if self.state == "running" and self.total and self.completed is not None:
            caption = f"{100*self.completed/self.total:.0f}%"
        action = self.create_text(w-10, 43, anchor="e", text=caption,
                         fill="#51db9e" if self.state != "waiting" else MUTED, font=(FONT, 8), tags="action")
        # Reserve the translated link's real width before drawing the summary.
        # Pixel fitting also works with mixed scripts, DPI changes and resizing.
        available = max(0, self.bbox(action)[0]-8-14)
        detail_font = font.Font(self, font=(FONT, 8))
        if number is not None:
            metric_font = font.Font(self, font=(FONT, 8, "bold"))
            number = self.fit_text(tr(number), metric_font, available)
            detail = self.fit_text(tr(detail), detail_font, available-metric_font.measure(number)-4)
            x = 13 + (detail_font.measure(detail)+4 if detail else 0)
            self.create_text(x, 43, anchor="w", text=number, fill="#fe5959", font=(FONT, 8, "bold"), tags="metric")
        else:
            detail = self.fit_text(tr(detail), detail_font, available)
        self.create_text(13, 43, anchor="w", text=detail, fill=detail_color, font=(FONT, 8), tags="detail")
        if self.state == "running":
            self.animate(time.monotonic()-self.home.animation_started)

    @staticmethod
    def fit_text(text, text_font, available):
        """Keep summaries on one line without entering the link's reserved area."""
        if text_font.measure(text) <= available:
            return text
        if text_font.measure("…") > available:
            return ""
        low, high = 0, len(text)
        while low < high:
            mid = (low+high+1)//2
            if text_font.measure(text[:mid].rstrip()+"…") <= available:
                low = mid
            else:
                high = mid-1
        return text[:low].rstrip()+"…"

    def _language_changed(self):
        self.paint()

    def animate(self, elapsed):
        self.delete("sweep")
        w, h = self.winfo_width(), self.winfo_height()
        for side in range(4):
            phase = (elapsed-side*.25) % 1
            if phase > .5:
                continue
            length = w if side % 2 == 0 else h
            end = (-1+4*phase)*length
            for step, color in enumerate(("#dcf8e9", "#c4f3da", "#9bebc7", "#72e4b4", "#51db9e")):
                a, b = max(2, end-length*.5+step*length*.1), min(length-2, end-length*.4+step*length*.1)
                if b <= a:
                    continue
                coords = ((a, 3, b, 3), (w-3, a, w-3, b),
                          (w-b, h-3, w-a, h-3), (3, h-b, 3, h-a))[side]
                self.create_line(*coords, fill=color, width=3, tags="sweep")


class HomePage(tk.Frame):
    def __init__(self, parent, application):
        super().__init__(parent, bg=WHITE)
        self.app, self.scanning, self.scanned, self._destroyed = application, False, False, False
        self.results, self.tiles, self.adopted = {}, {}, set()
        self._timer, self._repaint_after, self._last_report = None, None, {}
        self._last_running = None
        self.animation_started = time.monotonic()
        self.canvas = tk.Canvas(self, bg=WHITE, bd=0, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.checks = check_images(self)
        self.background = self.app.images.get("home/background.png")
        self.background_scaled, self._background_zoom = self.background, 1
        self.header_icon = self.app.images.get("home/search.png")
        self.heading = tk.StringVar(value="发现更多可清理的磁盘空间")
        self.headline = HomeHeadline(self.canvas, self, textvariable=self.heading, font=(FONT, 16, "bold"),
                                    bg=WHITE, fg="#303030", anchor="center", pady=4)
        self._heading_window = self.canvas.create_window(0, 0, window=self.headline, anchor="n")
        for key in home_scan.FEATURES:
            self.tiles[key] = HomeTile(self.canvas, self, key)
            self.canvas.create_window(0, 0, window=self.tiles[key], anchor="nw", tags=f"tile-{key}")
        self.scan_button = HomeButton(self.canvas, self, "一键扫描", self.primary, width=150, height=43)
        self._button_window = self.canvas.create_window(0, 0, window=self.scan_button, anchor="nw")
        self.rescan_button = HomeLink(self.canvas, self, self.scan)
        self._rescan_window = self.canvas.create_window(0, 0, window=self.rescan_button, anchor="nw", state="hidden")
        self.disk = HomeDisk(self.canvas, self)
        self._disk_window = self.canvas.create_window(0, 0, window=self.disk, anchor="nw")
        self.status = tk.StringVar(value="")
        self.canvas.bind("<Configure>", self.layout)
        self._timer = self.after(50, self.tick)

    def backdrop(self, target, x=0, y=0, tag="background"):
        if self.background_scaled:
            target.create_image(-x, -y, image=self.background_scaled, anchor="nw", tags=tag)

    def layout(self, event=None):
        if self._destroyed:
            return
        w, h = max(650, self.canvas.winfo_width()), max(510, self.canvas.winfo_height())
        self.canvas.delete("scene")
        if self.background:
            zoom = max(1, math.ceil(max(w/self.background.width(), h/self.background.height())))
            if zoom != self._background_zoom:
                self.background_scaled = self.background.zoom(zoom)
                self._background_zoom = zoom
        margin, grid_width, gap = 42, min(410, (w-84)*.57), 26
        tile_width = (grid_width-gap)/2
        tile_height = 66
        grid_y = min(124, max(100, h*.20))
        self.art_center = (margin+grid_width+w-margin)/2
        self.art_top = grid_y+27
        self.backdrop(self.canvas, tag="scene")
        self.paint_core()
        for index, key in enumerate(home_scan.FEATURES):
            self.canvas.coords(f"tile-{key}", margin+(index%2)*(tile_width+gap), grid_y+(index//2)*84)
            self.canvas.itemconfigure(f"tile-{key}", width=grid_width if len(home_scan.FEATURES)%2 and index == len(home_scan.FEATURES)-1 else tile_width, height=tile_height)
        self.canvas.coords(self._heading_window, w/2, 36)
        self.canvas.itemconfigure(self._heading_window, width=min(w-65, 680))
        self.headline.configure(font=(FONT, 14 if w < 850 else 16, "bold"))
        self.canvas.coords(self._button_window, self.art_center-int(self.scan_button.cget("width"))/2, self.art_top+190)
        self.canvas.coords(self._rescan_window, self.art_center-65, self.art_top+240)
        self.canvas.coords(self._disk_window, 120 if w >= 850 else 76, h-92)
        self.canvas.itemconfigure(self._disk_window, width=w-(210 if w >= 850 else 144))
        self.canvas.create_text(w/2, h-25, text=self.status.get(), fill=MUTED, font=(FONT, 9),
                                width=w-70, justify="center", tags=("scene", "status"))
        self.canvas.tag_lower("scene")
        if self._repaint_after is not None:
            self.after_cancel(self._repaint_after)
        self._repaint_after = self.after_idle(self.repaint_surfaces)

    def repaint_surfaces(self):
        self._repaint_after = None
        if not self._destroyed:
            self.scan_button.paint()
            self.rescan_button.paint()
            self.disk.paint()
            self.headline._paint()

    def paint_core(self, elapsed=None):
        if not hasattr(self, "art_center"):
            return
        state = "scanning" if self.scanning else "done" if self.scanned and not getattr(self, "_scan_cancelled", False) else "idle"
        if elapsed is None:
            started = getattr(self, "_finished_at", self.animation_started) if state == "done" else self.animation_started
            elapsed = time.monotonic()-started
        available = 2*(self.canvas.winfo_width()-42-self.art_center)
        home_core.draw(self.canvas, self.art_center, self.art_top,
                         elapsed, state, scale=min(1, max(.7, available/240)))

    def tick(self):
        self._timer = None
        if self._destroyed:
            return
        if self.scanning and self.winfo_ismapped():
            elapsed = time.monotonic()-self.animation_started
            for tile in self.tiles.values():
                if tile.state == "running":
                    tile.animate(elapsed)
        if self.winfo_ismapped():
            self.paint_core()
        self._timer = self.after(40 if self.scanning and self.winfo_ismapped() else 200, self.tick)

    def set_status(self, text):
        self.status.set(text)
        self.canvas.itemconfigure("status", text=text)

    def primary(self):
        if self.scanning:
            self.app.cancel.set()
            self.scan_button.caption = "正在取消"
            self.scan_button.set_enabled(False)
        elif self.scanned:
            order = ("cdrive", "large", "chat", "privacy", "compress", "popup", "migrate", "uninstall")
            key = next((key for key in order if self.tiles[key].selected and
                        self.tiles[key].state in ("done", "cancelled") and self.tiles[key].value), None)
            if key:
                self.open_feature(key)
            else:
                self.set_status("未发现可处理项目，可重新扫描或进入各功能查看。")
        else:
            self.scan()

    def scan(self):
        if self.app.busy or self.scanning:
            return
        selected = {key for key, tile in self.tiles.items() if tile.selected}
        if not selected:
            self.set_status("请先勾选要扫描的功能。")
            return
        folders = {}
        self.results.clear()
        self.adopted.clear()
        self.scanning, self.scanned = True, False
        self._last_report.clear()
        self._last_running = None
        self.animation_started = time.monotonic()
        for key, tile in self.tiles.items():
            tile.state = "waiting" if key in selected else "idle"
            if key in selected:
                self.app.set_nav_metric(key, None)
            tile.value, tile.completed, tile.total, tile.message = 0, None, None, ""
            tile.paint()
        self.scan_button.caption = "取消扫描"
        self.scan_button.set_enabled(True)
        self.canvas.itemconfigure(self._rescan_window, state="hidden")
        self.set_status("正在扫描，只读取信息")
        self.update_heading()
        def done(result):
            if self._destroyed:
                return
            self.results = result["results"]
            self.finish(bool(result.get("cancelled")))
        def failed(error):
            if not self._destroyed:
                self.finish(True)
                self.set_status(f"扫描未完成：{error}")
        self.app.start("首页一键扫描", lambda: home_scan.scan(self.app.cancel, self.report_progress,
                                                            folders=folders, enabled=selected), done, failed)

    def report_progress(self, data):
        key, now = data["key"], time.monotonic()
        if data["state"] == "running":
            if self._last_running == key and now-self._last_report.get(key, 0) < .1:
                return
            self._last_report[key], self._last_running = now, key
        elif data["state"] != "waiting":
            self._last_running = None
        self.app.events.put(("batch", (self.consume_progress, dict(data))))

    def consume_progress(self, data):
        if self._destroyed or not self.scanning:
            return
        tile = self.tiles[data["key"]]
        tile.state, tile.message = data["state"], data.get("message", "")
        tile.completed, tile.total = data.get("completed"), data.get("total")
        if "value" in data:
            tile.value = data["value"]
        if tile.state in ("done", "cancelled"):
            self.app.set_nav_metric(tile.key, tile.value, home_scan.UNITS[tile.key])
        tile.paint()
        if tile.state != "waiting":
            self.set_status(f"{home_scan.LABELS[tile.key]} · {tile.message}"[:100])
        self.update_heading()

    def finish(self, cancelled=False):
        self.scanning, self.scanned = False, True
        self._finished_at = time.monotonic()
        self._scan_cancelled = cancelled or any(feature.get("error") for feature in self.results.values())
        self.paint_core()
        for key, tile in self.tiles.items():
            feature = self.results.get(key)
            if feature:
                tile.value = feature["value"]
                tile.state = "failed" if feature.get("error") else "cancelled" if feature.get("cancelled") else "done"
                tile.message = feature.get("message", "")
                self.app.set_nav_metric(key, None if feature.get("error") else tile.value, home_scan.UNITS[key])
            elif tile.state in ("running", "waiting"):
                tile.state = "idle"
            tile.paint()
        failures = sum(bool(feature.get("error")) for feature in self.results.values())
        self.set_status(("扫描已取消，保留已找到的结果" if cancelled else "扫描完成") +
                        (f"，{failures}项未完成，可进入对应页面重试" if failures else ""))
        self.scan_button.caption = "立即处理"
        self.scan_button.set_enabled(True)
        self.canvas.itemconfigure(self._rescan_window, state="normal")
        self.refresh()
        current = self.app.current_page
        feature = self.results.get(current)
        if feature and not feature.get("error"):
            from home_results import apply_result
            if apply_result(self.app, current, feature):
                self.adopted.add(current)
                self.app.navigate(current, use_existing=True)

    def open_feature(self, key):
        if self.scanning or self.app.busy:
            return
        from home_results import apply_result
        feature = self.results.get(key)
        adopted = key in self.adopted
        if not adopted and feature:
            adopted = apply_result(self.app, key, feature)
            if adopted:
                self.adopted.add(key)
        if not adopted and key not in {"migrate", "compress", "chat"} and self.tiles[key].state in ("idle", "failed"):
            page = self.app.ensure_view(key)
            pending = getattr(page, "_pending_open", None)
            if pending is not None:
                page.after_cancel(pending)
                page._pending_open = None
            self.app.navigate(key, use_existing=True)
            page.scan()
            return
        self.app.navigate(key, use_existing=adopted)

    def invalidate(self, key):
        self.results.pop(key, None)
        self.adopted.discard(key)

    def update_heading(self):
        if self.scanning:
            completed = sum(tile.state in ("done", "cancelled", "failed") for tile in self.tiles.values() if tile.selected)
            total = sum(tile.selected for tile in self.tiles.values())
            self.heading.set(f"正在扫描电脑，已完成 {completed} / {total} 项")
            return
        selected = {key: value for key, value in self.results.items() if self.tiles[key].selected}
        for key in ("cdrive", "chat"):
            page = self.app.views.get(key) if hasattr(self.app, "views") else None
            if (key not in selected and self.tiles[key].selected and page and page.session
                    and self.tiles[key].state in ("done", "cancelled") and key in self.app.nav_metrics):
                selected[key] = dict(payload=dict(items=[item for category in page.session.categories for item in category.items]))
        amount = home_scan.cleanup_bytes(selected)
        self.heading.set(f"{'正在扫描，已发现' if self.scanning else '发现共计'} {size_text(amount)} 可清理磁盘空间"
                         if self.scanning or self.scanned or amount else "发现更多可清理的磁盘空间")

    def refresh(self):
        try:
            self.disk.update_info(disk_snapshot())
        except OSError:
            self.disk.info = None
            self.disk.paint()
        if not self.scanning:
            for key, tile in self.tiles.items():
                metric = self.app.nav_metrics.get(key)
                if metric and tile.state != "failed":
                    if key in self.results and self.results[key]["value"] != metric["value"]:
                        self.results.pop(key)
                    tile.value = metric["value"]
                    if tile.state != "cancelled":
                        tile.state = "done"
                    tile.paint()
            self.update_heading()

    def destroy(self):
        self._destroyed = True
        if self._timer is not None:
            self.after_cancel(self._timer)
            self._timer = None
        if self._repaint_after is not None:
            self.after_cancel(self._repaint_after)
            self._repaint_after = None
        super().destroy()
        self.heading, self.status = None, None
