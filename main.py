import socket
import threading
import cv2
import numpy as np
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.image import Image
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.label import Label
from kivy.uix.slider import Slider
from kivy.clock import Clock
from kivy.graphics.texture import Texture
from kivy.core.window import Window
from kivy.utils import get_color_from_hex, platform
from kivy.metrics import dp, sp

# --- CONFIGURATION ---
PORT = 8080
Window.size = (1280, 720)
Window.clearcolor = get_color_from_hex("#0b0f1a")
if platform != "android":
    Window.allow_resize = False

if platform == "android":
    try:
        from jnius import autoclass
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        ActivityInfo = autoclass("android.content.pm.ActivityInfo")
        activity = PythonActivity.mActivity
        activity.setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE)
    except Exception:
        pass

# --- NEON COLORS ---
C_CYAN = get_color_from_hex("#25f0ff")
C_PINK = get_color_from_hex("#ff58c7")
C_YELLOW = get_color_from_hex("#ffd24a")
C_GREEN = get_color_from_hex("#30d981")
C_RED = get_color_from_hex("#ff5b6b")
C_DARK = (0.12, 0.12, 0.16, 1)
C_WHITE = (1, 1, 1, 1)
C_SCROLL_ACTIVE = get_color_from_hex("#3d4a63")
C_SCROLL_INACTIVE = get_color_from_hex("#202738")

# --- NETWORK SCANNER ---
class NetworkScanner(threading.Thread):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self._stop = threading.Event()
        self._found = threading.Event()

    def request_stop(self):
        self._stop.set()

    def run(self):
        self._stop.clear()
        self._found.clear()
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('10.255.255.255', 1))
            my_ip = s.getsockname()[0]
        except: my_ip = '127.0.0.1'
        s.close()
        
        base = ".".join(my_ip.split(".")[:-1]) + "."
        found = None
        
        def check(ip):
            nonlocal found
            if found or self._stop.is_set() or self._found.is_set():
                return
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.2)
                if sock.connect_ex((ip, PORT)) == 0:
                    found = ip
                    self._found.set()
                sock.close()
            except: pass

        threads = []
        for i in range(1, 255):
            if self._stop.is_set() or self._found.is_set():
                break
            t = threading.Thread(target=check, args=(base + str(i),))
            t.start(); threads.append(t)
        for t in threads: t.join()
        Clock.schedule_once(lambda dt: self.callback(found), 0)

# --- MAIN APP ---
class StrikerApp(App):
    def build(self):
        self.sock = None
        self.is_connected = False
        self.capture = None
        self._video_thread = None
        self._video_stop = threading.Event()
        self._frame_lock = threading.Lock()
        self._last_frame = None
        self._video_url = ""
        self.title = "NEXUS CORE"
        self._scan_in_progress = False
        self._scanner_thread = None
        self.active_dirs = set()
        self.last_turn = None

        root = BoxLayout(orientation='vertical', padding=dp(8), spacing=dp(8))

        # === TOP: CONTROLS (SCROLL) ===
        controls_scroll = ScrollView(
            size_hint_y=0.45,
            do_scroll_x=False,
            bar_width=dp(6),
            bar_color=C_SCROLL_ACTIVE,
            bar_inactive_color=C_SCROLL_INACTIVE,
            scroll_type=["bars", "content"],
        )
        controls_panel = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(8), padding=(dp(6), dp(6)))
        controls_panel.bind(minimum_height=controls_panel.setter('height'))
        
        # 1. Header
        header = BoxLayout(size_hint_y=None, height=dp(40))
        lbl = Label(text="NEXUS CORE", font_size=sp(22), bold=True, color=C_CYAN, halign='left')
        lbl.bind(size=lbl.setter('text_size'))
        self.led = Button(background_normal='', background_color=C_RED, size_hint=(None, None), size=(dp(20), dp(20)))
        header.add_widget(lbl); header.add_widget(self.led)
        controls_panel.add_widget(header)

        # 2. Connection
        conn_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        self.txt_ip = TextInput(hint_text='ROBOT IP', multiline=False, 
                    background_color=(0.15,0.18,0.22,1), foreground_color=C_WHITE,
                    cursor_color=C_CYAN, hint_text_color=(0.6,0.6,0.6,1))
        
        self.btn_scan = Button(text='SCAN', size_hint_x=0.32, bold=True,
                       background_normal='', background_color=C_YELLOW, color=(0,0,0,1), font_size=sp(14))
        self.btn_scan.bind(on_press=self.scan_network)
        
        self.btn_con = Button(text='CONNECT', size_hint_x=0.42, bold=True,
                      background_normal='', background_color=C_RED, color=C_WHITE, font_size=sp(14))
        self.btn_con.bind(on_press=self.toggle_connection)
        
        conn_row.add_widget(self.txt_ip); conn_row.add_widget(self.btn_scan); conn_row.add_widget(self.btn_con)
        controls_panel.add_widget(conn_row)

        cam_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        self.txt_cam = TextInput(hint_text='CAMERA URL', multiline=False,
                 background_color=(0.12,0.14,0.18,1), foreground_color=C_WHITE,
                     cursor_color=C_CYAN, hint_text_color=(0.5,0.5,0.5,1))

        self.btn_cam = Button(text='START FEED', size_hint_x=0.34, bold=True,
                  background_normal='', background_color=C_CYAN, color=(0,0,0,1), font_size=sp(14))
        self.btn_cam.bind(on_press=self.toggle_video)
        cam_row.add_widget(self.txt_cam); cam_row.add_widget(self.btn_cam)
        controls_panel.add_widget(cam_row)

        # 3. Speed
        spd_row = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(70))
        self.lbl_spd = Label(text="SPEED: 100%", bold=True, color=C_WHITE, font_size=sp(14))
        self.slider = Slider(min=0, max=255, value=255)
        self.slider.bind(value=self.update_speed)
        spd_row.add_widget(self.lbl_spd); spd_row.add_widget(self.slider)
        controls_panel.add_widget(spd_row)

        # 4. Kickers
        lbl_k = Label(text="KICKERS", color=(0.55,0.55,0.6,1), size_hint_y=None, height=dp(18), font_size=sp(12))
        controls_panel.add_widget(lbl_k)
        
        act_grid = GridLayout(cols=2, spacing=dp(8), size_hint_y=None, height=dp(100))
        act_grid.add_widget(self.mk_act("PASS L", "N", C_PINK))
        act_grid.add_widget(self.mk_act("SHOOT L", "M", C_PINK))
        act_grid.add_widget(self.mk_act("PASS R", "P", C_CYAN))
        act_grid.add_widget(self.mk_act("SHOOT R", "K", C_CYAN))
        controls_panel.add_widget(act_grid)

        # 5. Reset
        btn_rst = Button(text="RESET SERVOS (X)", size_hint_y=None, height=dp(36),
                 background_normal='', background_color=(1,0.55,0.75,0.2), color=(0.75,0.75,0.8,1),
                 font_size=sp(13))
        btn_rst.bind(on_press=lambda x: self.send_command("X", "Reset"))
        controls_panel.add_widget(btn_rst)

        # 6. Log
        self.log_box = TextInput(text="> SYSTEM READY...", readonly=True, 
                     background_color=get_color_from_hex("#1b2333"), 
                     foreground_color=C_CYAN, font_size=sp(12))
        self.log_box.size_hint_y = None
        self.log_box.height = dp(120)
        controls_panel.add_widget(self.log_box)

        controls_scroll.add_widget(controls_panel)

        # === BOTTOM: VIDEO + MOVEMENT ===
        bottom_panel = BoxLayout(orientation='horizontal', size_hint_y=0.55, spacing=dp(8))

        left_move = BoxLayout(orientation='vertical', size_hint_x=0.18, spacing=dp(8))
        left_move.add_widget(self.mk_move_btn("F", "F"))
        left_move.add_widget(self.mk_move_btn("B", "B"))

        center_video = BoxLayout(orientation='vertical', size_hint_x=0.64)
        self.img_feed = Image(allow_stretch=True, keep_ratio=True)
        self.img_feed.color = (1,1,1,1)
        center_video.add_widget(self.img_feed)

        right_move = BoxLayout(orientation='vertical', size_hint_x=0.18, spacing=dp(8))
        right_move.add_widget(self.mk_move_btn("L", "L"))
        right_move.add_widget(self.mk_move_btn("R", "R"))

        bottom_panel.add_widget(left_move)
        bottom_panel.add_widget(center_video)
        bottom_panel.add_widget(right_move)

        root.add_widget(controls_scroll)
        root.add_widget(bottom_panel)
        Clock.schedule_interval(self.update_video, 1.0/30.0)
        return root

    # --- UI HELPERS ---
    def mk_move_btn(self, txt, cmd):
        b = Button(text=txt, font_size=sp(26), bold=True, background_normal='', background_color=C_GREEN, color=C_WHITE)
        b.bind(on_press=lambda x: self.set_dir(cmd, True))
        b.bind(on_release=lambda x: self.set_dir(cmd, False))
        return b

    def mk_act(self, txt, cmd, col):
        bg = (col[0], col[1], col[2], 0.15)  # Full opacity
        b = Button(text=txt, font_size=sp(14), bold=True, background_normal='', background_color=bg, color=col)
        b.bind(on_press=lambda x: self.send_command(cmd, txt))
        return b

    def log(self, msg):
        self.log_box.text += f"\n> {msg}"
        self.log_box.cursor = (0, len(self.log_box.text))

    def set_dir(self, cmd, pressed):
        if pressed:
            self.active_dirs.add(cmd)
            if cmd in ("L", "R"):
                self.last_turn = cmd
        else:
            self.active_dirs.discard(cmd)
        self.update_movement_command()

    def update_movement_command(self):
        if "L" in self.active_dirs or "R" in self.active_dirs:
            if "L" in self.active_dirs and "R" in self.active_dirs:
                cmd = "L" if self.last_turn != "R" else "R"
            else:
                cmd = "L" if "L" in self.active_dirs else "R"
            self.send_command(cmd, "Turn")
            return
        if "F" in self.active_dirs and "B" in self.active_dirs:
            self.send_command("S", "Stop")
        elif "F" in self.active_dirs:
            self.send_command("F", "Forward")
        elif "B" in self.active_dirs:
            self.send_command("B", "Backward")
        else:
            self.send_command("S", "Stop")

    # --- LOGIC ---
    def scan_network(self, instance):
        if self._scan_in_progress and self._scanner_thread and self._scanner_thread.is_alive():
            return
        self._scan_in_progress = True
        self.log("Scanning...")
        self.btn_scan.text = "..."
        self.btn_scan.disabled = True
        self._scanner_thread = NetworkScanner(self.on_scan)
        self._scanner_thread.start()

    def on_scan(self, ip):
        self.btn_scan.text = "SCAN"
        self.btn_scan.disabled = False
        self._scan_in_progress = False
        if ip:
            self.log(f"FOUND: {ip}")
            self.txt_ip.text = ip
            if not self.txt_cam.text:
                self.txt_cam.text = f"http://{ip}:4747/mjpegfeed"
            if not self.is_connected:
                self.toggle_connection(None)
        else:
            self.log("Not found.")

    def toggle_connection(self, instance):
        if not self.is_connected:
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.settimeout(2); self.sock.connect((self.txt_ip.text, PORT))
                self.is_connected = True; self.led.background_color = C_GREEN
                self.btn_con.text = "DISCONNECT"; self.btn_con.background_color = C_GREEN
                self.log("Connected!")
            except: self.log("Connect Failed")
        else: self.disconnect()

    def disconnect(self):
        if self.sock: self.sock.close()
        self.is_connected = False; self.led.background_color = C_RED
        self.btn_con.text = "CONNECT"; self.btn_con.background_color = C_RED
        self.log("Disconnected")

    def toggle_video(self, instance):
        if self._video_thread and self._video_thread.is_alive():
            self._video_stop.set()
            self._video_thread.join(timeout=1.0)
            self._video_thread = None
            self.btn_cam.text = "START FEED"
            self.btn_cam.background_color = C_CYAN
            self.img_feed.texture = None
            with self._frame_lock:
                self._last_frame = None
            if self.capture:
                self.capture.release()
                self.capture = None
        else:
            url = self.txt_cam.text.strip()
            if not url:
                self.log("Camera URL missing")
                return
            self._video_url = url
            self._video_stop.clear()
            self._video_thread = threading.Thread(target=self.video_worker, daemon=True)
            self._video_thread.start()
            self.btn_cam.text = "STOP FEED"
            self.btn_cam.background_color = C_RED

    def update_video(self, dt):
        with self._frame_lock:
            frame = self._last_frame
        if frame is not None:
            buf = cv2.flip(frame, 0).tobytes()
            tex = Texture.create(size=(frame.shape[1], frame.shape[0]), colorfmt='bgr')
            tex.blit_buffer(buf, colorfmt='bgr', bufferfmt='ubyte')
            self.img_feed.texture = tex

    def video_worker(self):
        retry_count = 0
        while not self._video_stop.is_set():
            if self.capture is None:
                try:
                    self.capture = cv2.VideoCapture(self._video_url, cv2.CAP_FFMPEG)
                except Exception:
                    self.capture = cv2.VideoCapture(self._video_url)
                if not self.capture or not self.capture.isOpened():
                    if self.capture:
                        self.capture.release()
                        self.capture = None
                    retry_count += 1
                    if retry_count == 1 or retry_count % 5 == 0:
                        Clock.schedule_once(lambda dt: self.log("Video open failed, retrying..."), 0)
                    self._video_stop.wait(0.5)
                    continue
                retry_count = 0
                Clock.schedule_once(lambda dt: self.log("Video ONLINE"), 0)

            ret, frame = self.capture.read()
            if ret:
                with self._frame_lock:
                    self._last_frame = frame
            else:
                if self.capture:
                    self.capture.release()
                    self.capture = None
                self._video_stop.wait(0.2)

    def send_command(self, cmd, desc=""):
        if self.is_connected:
            try: self.sock.send(cmd.encode())
            except: self.disconnect()
        if desc: self.log(f"Sent: {desc}")

    def update_speed(self, i, v):
        self.lbl_spd.text = f"SPEED: {int((v/255)*100)}%"
        self.send_command(f"V{int(110 + (v/255)*145) if v>0 else 0}")

if __name__ == '__main__':
    StrikerApp().run()
