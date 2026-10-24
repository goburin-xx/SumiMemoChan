import tkinter as tk
from tkinter import ttk
import datetime
import os
import threading
import webbrowser
import re
import ctypes
from ctypes import wintypes
import sys
import winreg
from PIL import Image, ImageDraw
import pystray


# Windowsのモニター座標とTkinterの座標を合わせる
if os.name == "nt":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def get_monitor_rects():
    """各モニターの物理座標 (left, top, right, bottom, primary) を返す。"""
    if os.name != "nt":
        return [(0, 0, 1920, 1080, True)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    monitors = []
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )

    def callback(hmonitor, hdc, rect_ptr, data):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if ctypes.windll.user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            r = info.rcMonitor
            monitors.append((
                r.left, r.top, r.right, r.bottom,
                bool(info.dwFlags & 1),
            ))
        return True

    callback_fn = callback_type(callback)
    ctypes.windll.user32.EnumDisplayMonitors(
        None, None, callback_fn, 0
    )
    return monitors or [(0, 0, 1920, 1080, True)]


class SuminoMemo:
    def __init__(self, root):
        self.root = root
        self.root.title("隅っこメモちゃん")
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)
        self.current_menu = None

        self.width = 450
        self.height = 350
        self.monitors = get_monitor_rects()
        self.active_monitor_rect = next(
            (m for m in self.monitors if m[4]), self.monitors[0]
        )
        self.hide_after_id = None
        self.root.geometry(f"{self.width}x{self.height}+0+0")

        self.is_visible = False
        self.is_paused = False
        self.root.attributes("-alpha", 0.0)

        if getattr(sys, "frozen", False):
            app_dir = os.path.dirname(sys.executable)
        else:
            app_dir = os.path.dirname(os.path.abspath(__file__))
        self.file_path = os.path.join(app_dir, "memo.txt")
        self.autostart_value_name = "SuminoMemo"

        self.last_cpu_time = None
        self.last_wall_time = None

        self.setup_ui()
        self.load_memos()
        self.setup_tray()
        self.check_mouse_position()

    def setup_ui(self):
        self.root.configure(bg="#202020")

        input_frame = tk.Frame(self.root, bg="#202020", padx=10, pady=10)
        input_frame.pack(fill="x")

        self.entry = tk.Entry(
            input_frame, bg="#303030", fg="white", insertbackground="white",
            font=("MS Gothic", 12), bd=1, relief="flat"
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.entry.bind("<Return>", self.add_memo)

        clear_btn = tk.Button(
            input_frame, text="一括クリア", bg="#404040", fg="white",
            relief="flat", command=self.clear_all, font=("MS Gothic", 9), padx=5
        )
        clear_btn.pack(side="right", padx=(5, 0))

        self.status_frame = tk.Frame(self.root, bg="#202020", padx=10, pady=2)
        self.status_frame.pack(side="bottom", fill="x")

        # メモファイルを開くリンク（左側固定）
        self.file_link = tk.Label(
            self.status_frame,
            text="メモファイルを開く",
            bg="#202020",
            fg="#7da9d8",
            font=("MS Gothic", 8, "underline"),
            cursor="hand2"
        )
        self.file_link.pack(side="left")
        self.file_link.bind("<Button-1>", lambda e: self.open_memo_file())

        # CPU・メモリ使用率（右側固定）
        self.status_lbl = tk.Label(
            self.status_frame, text="", bg="#202020", fg="#888888",
            font=("MS Gothic", 9)
        )
        self.status_lbl.pack(side="right")

        self.canvas = tk.Canvas(self.root, bg="#252525", bd=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.root, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg="#252525")

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas_window = self.canvas.create_window(
            (0, 0), window=self.scrollable_frame, anchor="nw", width=self.width
        )
        self.canvas.bind("<Configure>", self._resize_canvas_content)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

    def _resize_canvas_content(self, event):
        self.canvas.itemconfigure(self.canvas_window, width=event.width)

    def _monitor_at_point(self, x, y):
        for monitor in self.monitors:
            left, top, right, bottom, _primary = monitor
            if left <= x < right and top <= y < bottom:
                return monitor
        return None

    def _place_on_monitor(self, monitor):
        left, top, right, bottom, _primary = monitor
        x = right - self.width
        y = top
        self.active_monitor_rect = monitor
        self.root.update_idletasks()
        if os.name == "nt":
            frame_id = self.root.tk.call("wm", "frame", self.root._w)
            hwnd = int(str(frame_id), 0)
            ctypes.windll.user32.SetWindowPos(
                hwnd, ctypes.c_void_p(-1), x, y, self.width, self.height,
                0x0010  # SWP_NOACTIVATE
            )
            self.root.update_idletasks()
        else:
            self.root.geometry(f"{self.width}x{self.height}+{max(0, x)}+{max(0, y)}")

    def _cancel_scheduled_hide(self):
        if self.hide_after_id is not None:
            try:
                self.root.after_cancel(self.hide_after_id)
            except tk.TclError:
                pass
            self.hide_after_id = None

    def check_mouse_position(self):
        if not self.is_paused:
            mouse_x = self.root.winfo_pointerx()
            mouse_y = self.root.winfo_pointery()
            monitor = self._monitor_at_point(mouse_x, mouse_y)

            if monitor is not None:
                left, top, right, bottom, _primary = monitor
                at_corner = (right - 12 <= mouse_x < right and top <= mouse_y < top + 12)
                if at_corner:
                    self._cancel_scheduled_hide()
                    if not self.is_visible or monitor != self.active_monitor_rect:
                        self.show_window(monitor)
                elif self.is_visible:
                    win_left = self.root.winfo_rootx()
                    win_top = self.root.winfo_rooty()
                    win_width = max(self.width, self.root.winfo_width())
                    win_height = max(self.height, self.root.winfo_height())
                    
                    inside_window = (
                        win_left <= mouse_x < win_left + win_width
                        and win_top <= mouse_y < win_top + win_height
                    )
                    
                    # カスタムメニューが開いている場合、メニューの中にもマウスがあるかチェック
                    inside_menu = False
                    if self.current_menu:
                        try:
                            m_left = self.current_menu.winfo_rootx()
                            m_top = self.current_menu.winfo_rooty()
                            m_width = self.current_menu.winfo_width()
                            m_height = self.current_menu.winfo_height()
                            inside_menu = (
                                m_left <= mouse_x < m_left + m_width
                                and m_top <= mouse_y < m_top + m_height
                            )
                        except Exception:
                            pass

                    # メモ内、またはカスタムメニュー内なら表示維持、どちらの枠外にも出たら即座に両方消す！
                    if self.current_menu and not inside_menu:
                        self._close_menu()
                    if inside_window or inside_menu:
                        self._cancel_scheduled_hide()
                    else:
                        self.hide_window()

        self.root.after(100, self.check_mouse_position)

    def show_window(self, monitor_rect=None):
        self._cancel_scheduled_hide()
        if monitor_rect is not None:
            self._place_on_monitor(monitor_rect)
        elif not self.is_visible:
            monitor = self._monitor_at_point(
                self.root.winfo_pointerx(), self.root.winfo_pointery()
            )
            if monitor is not None:
                self._place_on_monitor(monitor)
            else:
                self._place_on_monitor(self.active_monitor_rect)

        self.is_visible = True
        self.root.attributes("-alpha", 0.95)
        self.entry.focus_set()
        self.update_resource_usage()

    def hide_window(self):
        self._cancel_scheduled_hide()
        self._close_menu()
        self.last_cpu_time = None
        self.last_wall_time = None
        self.is_visible = False
        self.root.attributes("-alpha", 0.0)
        self.save_memos()

    def add_memo(self, event=None):
        text = self.entry.get().strip()
        if text:
            now = datetime.datetime.now()
            memo_data = {
                "date": now.strftime("%Y年%m月%d日"),
                "time": now.strftime("%H:%M"),
                "text": text,
                "done": False,
            }
            self.memos.insert(0, memo_data)
            self.entry.delete(0, "end")
            self.refresh_ui()
            self.save_memos()
        return "break"

    def _copy_text(self, text_widget):
        try:
            selected = text_widget.get("sel.first", "sel.last")
        except tk.TclError:
            selected = text_widget.get("1.0", "end-1c")
        self.root.clipboard_clear()
        self.root.clipboard_append(selected)
        self.root.update()

    # ✨ 完全制御可能な自作カスタムメニュー
    def _show_copy_menu(self, event, text_widget):
        self._close_menu()

        menu = tk.Toplevel(self.root)
        menu.attributes("-topmost", True)
        menu.overrideredirect(True)
        menu.configure(bg="#303030", bd=1, relief="solid", highlightbackground="#505050")
        self.current_menu = menu

        btn_style = {
            "bg": "#303030", "fg": "white", "activebackground": "#505050",
            "activeforeground": "white", "bd": 0, "font": ("MS Gothic", 10),
            "anchor": "w", "padx": 15, "pady": 5
        }

        tk.Button(menu, text="コピー", command=lambda: (self._copy_text(text_widget), self._close_menu()), **btn_style).pack(fill="x")
        
        import urllib.parse
        def search_web():
            try:
                selected = text_widget.get("sel.first", "sel.last")
            except tk.TclError:
                selected = text_widget.get("1.0", "end-1c")
            search_query = selected.strip()
            if search_query:
                encoded_query = urllib.parse.quote(search_query)
                webbrowser.open(f"https://www.google.com/search?q={encoded_query}")
            self._close_menu()

        tk.Button(menu, text="Webで検索", command=search_web, **btn_style).pack(fill="x")

        def select_all_cmd():
            text_widget.tag_add("sel", "1.0", "end-1c")
            text_widget.mark_set("insert", "1.0")
            self._close_menu()

        tk.Button(menu, text="すべて選択", command=select_all_cmd, **btn_style).pack(fill="x")

        # メニュー外への移動は check_mouse_position() が監視する。
        # bind_all("<Button>") で先にメニューを破棄すると、ボタンの command が実行されない。

        menu.geometry(f"+{event.x_root}+{event.y_root}")
        return "break"

    def _close_menu(self):
        if self.current_menu:
            try:
                self.current_menu.destroy()
            except Exception:
                pass
            self.current_menu = None

    def _copy_shortcut(self, event, text_widget):
        self._copy_text(text_widget)
        return "break"

    def _selectable_text(self, parent, text, done):
        widget = tk.Text(
            parent, height=1, wrap="word", bg="#252525",
            fg="#666666" if done else "white", insertbackground="white",
            font=("MS Gothic", 11, "overstrike") if done else ("MS Gothic", 11),
            bd=0, relief="flat", highlightthickness=0, padx=0, pady=1, cursor="arrow",
            selectbackground="#496a91", selectforeground="white",
        )
        widget.insert("1.0", text)

        if not done:
            url_pattern = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
            for match in url_pattern.finditer(text):
                start = f"1.0+{match.start()}c"
                end = f"1.0+{match.end()}c"
                tag = f"url_{match.start()}"
                widget.tag_add(tag, start, end)
                widget.tag_configure(tag, foreground="#66aaff", underline=0)
                widget.tag_bind(tag, "<Enter>", lambda e, w=widget, t=tag: w.tag_configure(t, underline=1))
                widget.tag_bind(tag, "<Leave>", lambda e, w=widget, t=tag: w.tag_configure(t, underline=0))
                url = match.group(0).rstrip(".,;:!?)】」』")
                widget.tag_bind(tag, "<Button-1>", lambda e, u=url: (webbrowser.open(u), "break")[1])
                widget.tag_bind(tag, "<Button-3>", lambda e, w=widget: self._show_copy_menu(e, w))

        widget.update_idletasks()
        widget.configure(state="disabled")

        widget.bind("<Control-c>", lambda e, w=widget: self._copy_shortcut(e, w))
        widget.bind("<Control-C>", lambda e, w=widget: self._copy_shortcut(e, w))
        widget.bind("<Button-3>", lambda e, w=widget: self._show_copy_menu(e, w))
        widget.bind("<Control-a>", lambda e, w=widget: self._select_all(e, w))
        widget.bind("<Control-A>", lambda e, w=widget: self._select_all(e, w))
        widget.bind("<MouseWheel>", self._on_text_mousewheel)
        widget.bind("<Button-4>", self._on_text_mousewheel)
        widget.bind("<Button-5>", self._on_text_mousewheel)

        widget.bind("<Configure>", lambda e, w=widget: self._adjust_text_height(w))
        self.root.after_idle(lambda w=widget: self._adjust_text_height(w))
        
        return widget

    def _adjust_text_height(self, widget):
        try:
            widget.configure(state="normal")
            widget.update_idletasks()
            display_lines = int(widget.count("1.0", "end-1c", "displaylines")[0])
            widget.configure(height=max(1, display_lines), state="disabled")
        except (tk.TclError, TypeError, IndexError):
            try:
                widget.configure(state="disabled")
            except tk.TclError:
                pass

    def _select_all(self, event, widget):
        widget.configure(state="normal")
        widget.tag_add("sel", "1.0", "end-1c")
        widget.configure(state="disabled")
        return "break"

    def _on_text_mousewheel(self, event):
        if getattr(event, "num", None) == 4:
            self.canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5:
            self.canvas.yview_scroll(1, "units")
        else:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        return "break"

    def refresh_ui(self):
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()

        current_date = ""
        for index, memo in enumerate(self.memos):
            if memo["date"] != current_date:
                current_date = memo["date"]
                date_lbl = tk.Label(
                    self.scrollable_frame, text=f"--- {current_date} ---",
                    bg="#252525", fg="#888888", font=("MS Gothic", 9, "bold"), pady=4
                )
                date_lbl.pack(fill="x")

            row = tk.Frame(self.scrollable_frame, bg="#252525", padx=10, pady=2)
            row.pack(fill="x")
            
            row.columnconfigure(2, weight=1)

            time_lbl = tk.Label(row, text=f"🕒 {memo['time']}", bg="#252525", fg="#aaaaaa", font=("MS Gothic", 10))
            time_lbl.grid(row=0, column=0, sticky="nw", padx=(0, 5))

            done_text = "[済]" if memo["done"] else "[ ]"
            done_color = "#55ff55" if memo["done"] else "#aaaaaa"
            done_btn = tk.Button(
                row, text=done_text, bg="#252525", fg=done_color, bd=0,
                activebackground="#333333", activeforeground="white",
                font=("MS Gothic", 10, "bold"), cursor="hand2",
                command=lambda i=index: self.toggle_done(i)
            )
            done_btn.grid(row=0, column=1, sticky="nw", padx=(0, 5))

            text_widget = self._selectable_text(row, memo["text"], memo["done"])
            text_widget.grid(row=0, column=2, sticky="ew", padx=5)

            del_btn = tk.Button(
                row, text="[消去]", bg="#252525", fg="#ff5555", bd=0,
                activebackground="#252525", activeforeground="#ff8888",
                font=("MS Gothic", 9), cursor="hand2",
                command=lambda i=index: self.delete_memo(i)
            )
            del_btn.grid(row=0, column=3, sticky="ne")

                    
    def open_memo_file(self):
        try:
            self.save_memos()
            os.startfile(self.file_path)
        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror(
                "エラー",
                f"メモファイルを開けませんでした。\n{e}"
            )
    def toggle_done(self, index):
        self.memos[index]["done"] = not self.memos[index]["done"]
        self.refresh_ui()
        self.save_memos()

    def delete_memo(self, index):
        self.memos.pop(index)
        self.refresh_ui()
        self.save_memos()

    def clear_all(self):
        self.memos = []
        self.refresh_ui()
        self.save_memos()

    def save_memos(self):
        with open(self.file_path, "w", encoding="utf-8") as f:
            for memo in self.memos:
                safe_text = memo["text"].replace("\r", " ").replace("\n", " ")
                f.write(f"{memo['date']}|{memo['time']}|{int(memo['done'])}|{safe_text}\n")

    def load_memos(self):
        self.memos = []
        if os.path.exists(self.file_path):
            with open(self.file_path, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.rstrip("\n").split("|", 3)
                    if len(parts) == 4:
                        try:
                            done = bool(int(parts[2]))
                        except ValueError:
                            done = False
                        self.memos.append({
                            "date": parts[0],
                            "time": parts[1],
                            "done": done,
                            "text": parts[3],
                        })
        self.refresh_ui()

    def setup_tray(self):
        image = Image.new("RGB", (64, 64), color="#22cc22")
        draw = ImageDraw.Draw(image)
        draw.text((20, 20), "S", fill="white")

        # Windows標準のトレイメニューを右クリックで開く（左クリック機能は追加しない）。
        menu = pystray.Menu(
            pystray.MenuItem(
                "Windows起動時に自動起動する", self.toggle_autostart,
                checked=lambda item: self.is_autostart_enabled()
            ),
            pystray.MenuItem(
                "お休みモード（一時停止）", self.toggle_pause,
                checked=lambda item: self.is_paused
            ),
            pystray.MenuItem("終了", self.quit_app),
        )

        self.tray_icon = pystray.Icon("SuminoMemo", image, "隅っこメモちゃん", menu)
        self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        self.tray_thread.start()

    def _autostart_command(self):
        """Windowsサインイン時に起動するコマンドを返す。"""
        if getattr(sys, "frozen", False):
            # PyInstallerでEXE化されている場合
            return f'"{sys.executable}"'

        # Pythonスクリプト実行時は、コンソールを出さない pythonw.exe を優先
        python_exe = sys.executable
        pythonw_exe = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
        if os.path.isfile(pythonw_exe):
            python_exe = pythonw_exe
        script_path = os.path.abspath(__file__)
        return f'"{python_exe}" "{script_path}"'

    def is_autostart_enabled(self):
        """現在のユーザーの自動起動登録状況を確認する。"""
        if os.name != "nt":
            return False
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_READ
            ) as key:
                value, _ = winreg.QueryValueEx(key, self.autostart_value_name)
            return bool(value)
        except (FileNotFoundError, OSError):
            return False

    def toggle_autostart(self, icon, item):
        """トレイメニューから自動起動の登録・解除を切り替える。"""
        if os.name != "nt":
            return
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE
            ) as key:
                if self.is_autostart_enabled():
                    winreg.DeleteValue(key, self.autostart_value_name)
                else:
                    winreg.SetValueEx(
                        key, self.autostart_value_name, 0, winreg.REG_SZ,
                        self._autostart_command()
                    )
        except OSError as exc:
            # 失敗時は状態を変えたように見せず、エラーを通知する
            try:
                error_message = str(exc)
                self.root.after(0, lambda message=error_message: __import__("tkinter.messagebox", fromlist=["showerror"]).showerror(
                    "自動起動の設定に失敗", f"自動起動を変更できませんでした。\n{message}"
                ))
            except Exception:
                pass

    def toggle_pause(self, icon, item):
        self.root.after(0, self._toggle_pause_tk)

    def _toggle_pause_tk(self):
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.hide_window()

    def force_show(self, icon, item):
        self.root.after(0, self._force_show_tk)

    def _force_show_tk(self):
        self.is_paused = False
        self.show_window()

    def update_resource_usage(self):
        if self.is_visible:
            try:
                if os.name == "nt":
                    import time
                    
                    class FILETIME(ctypes.Structure):
                        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

                    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                        _fields_ = [
                            ("cb", wintypes.DWORD),
                            ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PageFileUsage", ctypes.c_size_t),
                            ("PeakPageFileUsage", ctypes.c_size_t),
                        ]

                    PROCESS_QUERY_INFORMATION = 0x0400
                    PROCESS_VM_READ = 0x0010
                    h_process = ctypes.windll.kernel32.OpenProcess(
                        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, os.getpid()
                    )

                    if h_process:
                        pmc = PROCESS_MEMORY_COUNTERS()
                        pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
                        psapi = ctypes.WinDLL("psapi.dll")
                        
                        mem_str = ""
                        if psapi.GetProcessMemoryInfo(h_process, ctypes.byref(pmc), pmc.cb):
                            mem_mb = pmc.WorkingSetSize / (1024 * 1024)
                            mem_str = f"メモリ使用率: {mem_mb:.1f} MB"

                        creation = FILETIME()
                        exit_time = FILETIME()
                        kernel = FILETIME()
                        user = FILETIME()
                        
                        cpu_str = ""
                        if ctypes.windll.kernel32.GetProcessTimes(
                            h_process, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel), ctypes.byref(user)
                        ):
                            def filetime_to_int(ft):
                                return (ft.dwHighDateTime << 32) + ft.dwLowDateTime

                            kernel_time = filetime_to_int(kernel)
                            user_time = filetime_to_int(user)
                            total_cpu_time = kernel_time + user_time
                            current_wall_time = time.time()

                            if self.last_cpu_time is not None and self.last_wall_time is not None:
                                cpu_diff = total_cpu_time - self.last_cpu_time
                                wall_diff = (current_wall_time - self.last_wall_time) * 10_000_000
                                
                                if wall_diff > 0:
                                    cpu_usage = (cpu_diff / wall_diff) * 100 / os.cpu_count()
                                    cpu_usage = max(0.0, min(100.0, cpu_usage))
                                    cpu_str = f"CPU使用率: {cpu_usage:.1f}%"

                            self.last_cpu_time = total_cpu_time
                            self.last_wall_time = current_wall_time

                        ctypes.windll.kernel32.CloseHandle(h_process)

                        if cpu_str:
                            display_text = f"{cpu_str}  {mem_str}"
                        else:
                            display_text = f"CPU使用率計測中…  {mem_str}".strip()
                        self.status_lbl.configure(text=display_text)
            except Exception:
                self.status_lbl.configure(text="メモリ使用率: Err")

            self.root.after(3000, self.update_resource_usage)
        else:
            self.last_cpu_time = None
            self.last_wall_time = None
            self.status_lbl.configure(text="")    

    def quit_app(self, icon=None, item=None):
        try:
            self.root.after(0, self._quit_app_tk)
        except tk.TclError:
            pass

    def _quit_app_tk(self):
        self.save_memos()
        try:
            self.tray_icon.stop()
        except Exception:
            pass
        try:
            self.root.quit()
            self.root.destroy()
        except tk.TclError:
            pass


if __name__ == "__main__":
    root = tk.Tk()
    app = SuminoMemo(root)
    root.protocol("WM_DELETE_WINDOW", app.quit_app)
    root.mainloop()