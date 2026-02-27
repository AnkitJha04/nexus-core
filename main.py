import ipaddress
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Ellipse, Rectangle, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform


class Card(BoxLayout):
	def __init__(self, bg_color=(0.13, 0.18, 0.24, 1), radius=dp(14), **kwargs):
		super().__init__(**kwargs)
		self._bg_color = bg_color
		self._radius = radius
		self._bg = None
		with self.canvas.before:
			Color(*self._bg_color)
			self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[self._radius] * 4)
		self.bind(pos=self._update_bg, size=self._update_bg)

	def _update_bg(self, *_args):
		self._bg.pos = self.pos
		self._bg.size = self.size


class FixedAspectFrame(FloatLayout):
	def __init__(self, aspect_ratio=16 / 9, **kwargs):
		super().__init__(**kwargs)
		self.aspect_ratio = aspect_ratio
		self.content = None
		self.bind(pos=self._layout_content, size=self._layout_content)

	def set_content(self, widget):
		if self.content is not None:
			self.remove_widget(self.content)
		self.content = widget
		self.add_widget(widget)
		self._layout_content()

	def _layout_content(self, *_args):
		if not self.content:
			return
		width, height = self.size
		if width <= 0 or height <= 0:
			return
		target = self.aspect_ratio
		if width / height > target:
			content_h = height
			content_w = content_h * target
		else:
			content_w = width
			content_h = content_w / target
		self.content.size = (content_w, content_h)
		self.content.pos = (
			self.x + (width - content_w) / 2,
			self.y + (height - content_h) / 2,
		)


class Joystick(Widget):
	def __init__(self, on_move, on_release, axis="both", **kwargs):
		super().__init__(**kwargs)
		self._on_move = on_move
		self._on_release = on_release
		self._axis = axis
		self._base = None
		self._knob = None
		self._radius = 0
		self._knob_radius = 0
		self._center = (0, 0)
		self._knob_pos = (0, 0)

		with self.canvas:
			Color(0.12, 0.6, 0.68, 1)
			self._base = Ellipse()
			Color(0.98, 0.8, 0.2, 1)
			self._knob = Ellipse()

		self.bind(pos=self._update_graphics, size=self._update_graphics)

	def _update_graphics(self, *_args):
		size = min(self.width, self.height)
		self._radius = size * 0.4
		self._knob_radius = size * 0.18
		self._center = (self.center_x, self.center_y)
		self._knob_pos = self._center

		self._base.pos = (self._center[0] - self._radius, self._center[1] - self._radius)
		self._base.size = (self._radius * 2, self._radius * 2)
		self._knob.pos = (self._knob_pos[0] - self._knob_radius, self._knob_pos[1] - self._knob_radius)
		self._knob.size = (self._knob_radius * 2, self._knob_radius * 2)

	def on_touch_down(self, touch):
		if not self.collide_point(*touch.pos):
			return super().on_touch_down(touch)
		self._move_knob(touch.pos)
		touch.grab(self)
		return True

	def on_touch_move(self, touch):
		if touch.grab_current is not self:
			return super().on_touch_move(touch)
		self._move_knob(touch.pos)
		return True

	def on_touch_up(self, touch):
		if touch.grab_current is not self:
			return super().on_touch_up(touch)
		self._knob_pos = self._center
		self._knob.pos = (self._knob_pos[0] - self._knob_radius, self._knob_pos[1] - self._knob_radius)
		self._on_release()
		touch.ungrab(self)
		return True

	def _move_knob(self, pos):
		dx = pos[0] - self._center[0]
		dy = pos[1] - self._center[1]
		if self._axis == "vertical":
			dx = 0
		elif self._axis == "horizontal":
			dy = 0
		distance = (dx * dx + dy * dy) ** 0.5
		if distance > self._radius:
			scale = self._radius / distance
			dx *= scale
			dy *= scale
		self._knob_pos = (self._center[0] + dx, self._center[1] + dy)
		self._knob.pos = (self._knob_pos[0] - self._knob_radius, self._knob_pos[1] - self._knob_radius)

		magnitude = min(distance / self._radius, 1.0)
		self._on_move(dx, dy, magnitude)


class RcCarControllerApp(App):
	def __init__(self, **kwargs):
		super().__init__(**kwargs)
		self.sock = None
		self._status = None
		self._ip_input = None
		self._port_input = None
		self._connect_btn = None
		self._scan_btn = None
		self._scan_running = False
		self._last_vert = (None, 0)
		self._last_horiz = (None, 0)
		self._throttle_state = (None, 0)
		self._steer_state = (None, 0)
		self._last_dispatched = None
		self._target_ratio = 16 / 9

	def build(self):
		self._setup_display_mode()
		Window.softinput_mode = "below_target"
		Window.clearcolor = (0.03, 0.06, 0.1, 1)
		root = FloatLayout()

		with root.canvas.before:
			Color(0.04, 0.08, 0.13, 1)
			bg = Rectangle(pos=root.pos, size=root.size)
		root.bind(pos=lambda _i, _v: setattr(bg, "pos", root.pos))
		root.bind(size=lambda _i, _v: setattr(bg, "size", root.size))

		surface = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10), size_hint=(1, 1))
		root.add_widget(surface)

		main_row = BoxLayout(orientation="horizontal", spacing=dp(10), size_hint=(1, 1))
		surface.add_widget(main_row)

		left_controls = Card(
			orientation="vertical",
			spacing=dp(10),
			padding=dp(10),
			size_hint_x=0.34,
			bg_color=(0.1, 0.15, 0.2, 1),
		)
		main_row.add_widget(left_controls)

		throttle_pad = Card(orientation="vertical", spacing=dp(6), padding=dp(8), bg_color=(0.11, 0.18, 0.24, 1))
		throttle_pad.add_widget(Label(text="Throttle (Up / Down)", bold=True, size_hint_y=None, height=dp(28), color=(0.8, 0.9, 0.98, 1), font_size=sp(14)))
		left_joystick = Joystick(
			on_move=self.on_joystick_move_vertical,
			on_release=self.on_joystick_release_vertical,
			axis="vertical",
		)
		throttle_pad.add_widget(left_joystick)
		left_controls.add_widget(throttle_pad)

		middle_panel = BoxLayout(orientation="vertical", spacing=dp(10), size_hint_x=0.32)
		main_row.add_widget(middle_panel)

		right_controls = Card(
			orientation="vertical",
			spacing=dp(10),
			padding=dp(10),
			size_hint_x=0.34,
			bg_color=(0.1, 0.15, 0.2, 1),
		)
		main_row.add_widget(right_controls)

		steer_pad = Card(orientation="vertical", spacing=dp(6), padding=dp(8), bg_color=(0.11, 0.18, 0.24, 1))
		steer_pad.add_widget(Label(text="Steering (Left / Right)", bold=True, size_hint_y=None, height=dp(28), color=(0.8, 0.9, 0.98, 1), font_size=sp(14)))
		right_joystick = Joystick(
			on_move=self.on_joystick_move_horizontal,
			on_release=self.on_joystick_release_horizontal,
			axis="horizontal",
		)
		steer_pad.add_widget(right_joystick)
		right_controls.add_widget(steer_pad)

		header = Card(orientation="horizontal", spacing=dp(10), size_hint_y=None, height=dp(68), padding=(dp(14), dp(10)))
		title_box = BoxLayout(orientation="vertical")
		title_box.add_widget(Label(text="NEXUS DRIVE", bold=True, color=(0.85, 0.95, 1, 1), font_size=sp(22), halign="left", valign="middle"))
		title_box.add_widget(Label(text="Touch controls • Fast scan • Mobile-first", color=(0.65, 0.8, 0.9, 1), font_size=sp(12), halign="left", valign="middle"))
		for child in title_box.children:
			if isinstance(child, Label):
				child.bind(size=child.setter("text_size"))
		header.add_widget(title_box)
		middle_panel.add_widget(header)

		connect_panel = Card(orientation="vertical", spacing=dp(8), size_hint_y=None, height=dp(132), padding=dp(10))
		connect_row = BoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(50))
		self._ip_input = TextInput(
			hint_text="Device IP",
			multiline=False,
			size_hint_x=0.52,
			font_size=sp(16),
			padding=(dp(10), dp(12)),
			background_normal="",
			background_active="",
			background_color=(0.08, 0.12, 0.17, 1),
			foreground_color=(0.92, 0.96, 1, 1),
			hint_text_color=(0.45, 0.62, 0.74, 1),
		)
		self._port_input = TextInput(
			hint_text="Port",
			multiline=False,
			size_hint_x=0.21,
			input_filter="int",
			text="8080",
			font_size=sp(16),
			padding=(dp(10), dp(12)),
			background_normal="",
			background_active="",
			background_color=(0.08, 0.12, 0.17, 1),
			foreground_color=(0.92, 0.96, 1, 1),
			hint_text_color=(0.45, 0.62, 0.74, 1),
		)
		self._connect_btn = Button(text="Connect", size_hint_x=0.27, bold=True, font_size=sp(14), background_normal="", background_color=(0.2, 0.75, 0.62, 1))
		self._connect_btn.bind(on_release=self.on_connect)
		connect_row.add_widget(self._ip_input)
		connect_row.add_widget(self._port_input)
		connect_row.add_widget(self._connect_btn)
		connect_panel.add_widget(connect_row)

		scan_row = BoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=None, height=dp(46))
		self._scan_btn = Button(text="Scan IP", size_hint_x=0.34, bold=True, font_size=sp(14), background_normal="", background_color=(0.35, 0.58, 0.95, 1))
		self._scan_btn.bind(on_release=self.on_scan)
		self._status = Label(text="Not connected", halign="left", valign="middle", color=(1, 0.78, 0.3, 1), font_size=sp(14))
		self._status.bind(size=self._status.setter("text_size"))
		scan_row.add_widget(self._scan_btn)
		scan_row.add_widget(self._status)
		connect_panel.add_widget(scan_row)
		middle_panel.add_widget(connect_panel)

		middle_panel.add_widget(Widget(size_hint_y=1))

		extra_bar = Card(orientation="vertical", spacing=dp(6), size_hint_y=None, height=dp(78), padding=(dp(10), dp(10)), bg_color=(0.12, 0.18, 0.25, 1))
		extra_row = GridLayout(cols=4, spacing=dp(6), size_hint=(1, 1))
		extra_colors = [
			(0.94, 0.4, 0.4, 1),
			(0.35, 0.72, 0.98, 1),
			(0.38, 0.82, 0.5, 1),
			(0.95, 0.76, 0.32, 1),
		]
		for label, color in zip(["Extra 1", "Extra 2", "Extra 3", "Extra 4"], extra_colors):
			btn = Button(text=label, bold=True, font_size=sp(12), background_normal="", background_color=color)
			btn.bind(on_release=self.on_extra)
			extra_row.add_widget(btn)
		extra_bar.add_widget(extra_row)
		middle_panel.add_widget(extra_bar)

		return root

	def _setup_display_mode(self):
		if platform == "android":
			try:
				from jnius import autoclass
				PythonActivity = autoclass("org.kivy.android.PythonActivity")
				ActivityInfo = autoclass("android.content.pm.ActivityInfo")
				activity = PythonActivity.mActivity
				activity.setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE)
			except Exception:
				pass
		else:
			Window.minimum_width = 960
			Window.minimum_height = 540
			if Window.width / max(Window.height, 1) != self._target_ratio:
				Window.size = (1280, 720)

	def on_connect(self, _instance):
		if self.sock:
			self._close_socket()
			self._set_status("Disconnected")
			return

		ip = self._ip_input.text.strip()
		port = self._get_port()
		if not ip:
			self._set_status("Enter an IP to connect")
			return
		if port is None:
			return

		threading.Thread(target=self._connect_worker, args=(ip, port), daemon=True).start()

	def on_scan(self, _instance):
		if self._scan_running:
			self._set_status("Scan already running")
			return
		port = self._get_port()
		if port is None:
			return

		self._scan_running = True
		self._set_status("Scanning local network...")
		threading.Thread(target=self._scan_worker, args=(port,), daemon=True).start()

	def on_extra(self, instance):
		command = instance.text.upper().replace(" ", "_")
		self.send_command(command)

	def on_joystick_move_vertical(self, _dx, dy, magnitude):
		if magnitude < 0.15:
			self._update_direction("vertical", None, 0)
			return
		direction = "FRONT" if dy > 0 else "BACK"
		value = self._scale_analog(magnitude)
		self._update_direction("vertical", direction, value)

	def on_joystick_release_vertical(self):
		self._update_direction("vertical", None, 0)

	def on_joystick_move_horizontal(self, dx, _dy, magnitude):
		if magnitude < 0.15:
			self._update_direction("horizontal", None, 0)
			return
		direction = "RIGHT" if dx > 0 else "LEFT"
		value = self._scale_analog(magnitude)
		self._update_direction("horizontal", direction, value)

	def on_joystick_release_horizontal(self):
		self._update_direction("horizontal", None, 0)

	def _update_direction(self, axis, direction, value):
		if axis == "vertical":
			last_dir, last_val = self._last_vert
			if (direction, value) == (last_dir, last_val):
				return
			self._last_vert = (direction, value)
			self._throttle_state = (direction, value)
			self._dispatch_drive_commands()
			return

		last_dir, last_val = self._last_horiz
		if (direction, value) == (last_dir, last_val):
			return
		self._last_horiz = (direction, value)
		self._steer_state = (direction, value)
		self._dispatch_drive_commands()

	def _scale_analog(self, magnitude):
		value = 110 + int(round(magnitude * (255 - 110)))
		return min(max(value, 110), 255)

	def _dispatch_drive_commands(self):
		steer_dir, steer_val = self._steer_state
		throttle_dir, throttle_val = self._throttle_state

		commands = []
		if steer_dir is None:
			commands.append("STEER:0")
		else:
			commands.append(f"STEER:{steer_dir}:{steer_val}")

		if steer_dir is not None:
			commands.append("THROTTLE:0")
		elif throttle_dir is None:
			commands.append("THROTTLE:0")
		else:
			commands.append(f"THROTTLE:{throttle_dir}:{throttle_val}")

		dispatch_key = tuple(commands)
		if dispatch_key == self._last_dispatched:
			return
		self._last_dispatched = dispatch_key
		self.send_commands(commands)

	def send_command(self, command):
		self.send_commands([command])

	def send_commands(self, commands):
		if not commands:
			return
		if not self.sock:
			self._set_status(f"Not connected: {' | '.join(commands)}")
			return
		try:
			payload = "\n".join(commands) + "\n"
			self.sock.sendall(payload.encode("ascii"))
			self._set_status(f"Sent: {' | '.join(commands)}")
		except OSError:
			self._set_status("Connection lost")
			self._close_socket()

	def _connect_worker(self, ip, port):
		self._set_status_threadsafe(f"Connecting to {ip}:{port}...")
		try:
			sock = socket.create_connection((ip, port), timeout=3)
		except OSError:
			self._set_status_threadsafe("Connect failed")
			self._set_connected_ui_threadsafe(False)
			return

		self._close_socket()
		self.sock = sock
		self._set_status_threadsafe(f"Connected to {ip}:{port}")
		self._set_connected_ui_threadsafe(True)

	def _scan_worker(self, port):
		try:
			local_ip = self._get_local_ip()
			if not local_ip:
				self._set_status_threadsafe("Unable to get local IP")
				return

			network = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
			found = []

			def probe(host):
				try:
					sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
					sock.settimeout(0.25)
					result = sock.connect_ex((str(host), port))
					sock.close()
					return result == 0
				except OSError:
					return False

			def probe_udp(host):
				try:
					sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
					sock.settimeout(0.4)
					sock.sendto(b"PING", (str(host), port))
					sock.recvfrom(64)
					sock.close()
					return True
				except OSError:
					return False

			hosts = [host for host in network.hosts() if str(host) != local_ip]

			with ThreadPoolExecutor(max_workers=64) as executor:
				futures = {executor.submit(probe, host): host for host in hosts}
				for future in as_completed(futures):
					if future.result():
						found.append(str(futures[future]))

			if not found:
				self._set_status_threadsafe("No TCP devices found, trying UDP...")
				with ThreadPoolExecutor(max_workers=64) as executor:
					futures = {executor.submit(probe_udp, host): host for host in hosts}
					for future in as_completed(futures):
						if future.result():
							found.append(str(futures[future]))

			if found:
				first_ip = found[0]
				self._set_status_threadsafe(f"Found: {', '.join(found[:5])}")
				Clock.schedule_once(lambda _dt: setattr(self._ip_input, "text", first_ip))
			else:
				self._set_status_threadsafe("No devices found")
		except Exception:
			self._set_status_threadsafe("Scan failed")
		finally:
			self._scan_running = False

	def _get_local_ip(self):
		try:
			sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
			sock.connect(("10.255.255.255", 1))
			local_ip = sock.getsockname()[0]
			sock.close()
			if local_ip and not local_ip.startswith("127."):
				return local_ip
		except OSError:
			pass

		try:
			return socket.gethostbyname(socket.gethostname())
		except OSError:
			return None

	def _get_port(self):
		text = self._port_input.text.strip()
		if not text:
			self._set_status("Enter a port")
			return None
		try:
			port = int(text)
		except ValueError:
			self._set_status("Port must be a number")
			return None
		if port <= 0 or port > 65535:
			self._set_status("Port out of range")
			return None
		return port

	def _set_status(self, message):
		if self._status:
			self._status.text = message
			lower = message.lower()
			if "connected" in lower or "found" in lower or "sent" in lower:
				self._status.color = (0.42, 0.95, 0.58, 1)
			elif "scan" in lower or "trying" in lower or "connecting" in lower:
				self._status.color = (1, 0.8, 0.35, 1)
			else:
				self._status.color = (1, 0.45, 0.45, 1)

	def _set_status_threadsafe(self, message):
		Clock.schedule_once(lambda _dt: self._set_status(message))

	def _set_connected_ui(self, connected):
		if self._connect_btn:
			if connected:
				self._connect_btn.text = "Disconnect"
				self._connect_btn.background_color = (0.95, 0.45, 0.43, 1)
			else:
				self._connect_btn.text = "Connect"
				self._connect_btn.background_color = (0.2, 0.75, 0.62, 1)

	def _set_connected_ui_threadsafe(self, connected):
		Clock.schedule_once(lambda _dt: self._set_connected_ui(connected))

	def _close_socket(self):
		self._last_dispatched = None
		if self.sock:
			try:
				self.sock.close()
			except OSError:
				pass
			self.sock = None
		self._set_connected_ui_threadsafe(False)


if __name__ == "__main__":
	RcCarControllerApp().run()
