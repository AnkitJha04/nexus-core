import socket
import threading

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
from kivy.uix.widget import Widget
from kivy.utils import platform


class Card(BoxLayout):
	def __init__(self, bg_color=(0.13, 0.18, 0.24, 1), radius=dp(14), border_color=(0.36, 0.47, 0.72, 0.35), border_width=1.2, **kwargs):
		super().__init__(**kwargs)
		self._bg_color = bg_color
		self._radius = radius
		self._border_color = border_color
		self._border_width = border_width
		self._bg = None
		self._border = None
		with self.canvas.before:
			Color(*self._border_color)
			self._border = RoundedRectangle(pos=self.pos, size=self.size, radius=[self._radius] * 4)
			Color(*self._bg_color)
			self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[self._radius] * 4)
		self.bind(pos=self._update_bg, size=self._update_bg)

	def _update_bg(self, *_args):
		if self._border:
			self._border.pos = self.pos
			self._border.size = self.size
		self._bg.pos = (self.x + self._border_width, self.y + self._border_width)
		self._bg.size = (
			max(self.width - 2 * self._border_width, 0),
			max(self.height - 2 * self._border_width, 0),
		)


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
	def __init__(self, on_move, on_release, axis="both", base_color=(0.25, 0.41, 0.88, 1), knob_color=(0.88, 0.92, 1, 1), **kwargs):
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
		self._base_color = base_color
		self._knob_color = knob_color

		with self.canvas:
			Color(*self._base_color)
			self._base = Ellipse()
			Color(*self._knob_color)
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
		self._esp_ip = "192.168.4.1"
		self._esp_port = 8080
		self.sock = None
		self._status = None
		self._cmd_status = None
		self._encoder_status = None
		self._connecting = False
		self._reader_running = False
		self._joy_x = 0
		self._joy_y = 0
		self._last_drive_cmd = None
		self._axis_deadzone = 0.08
		self._target_ratio = 16 / 9
		self._surface = None
		self._top_bar = None
		self._main_row = None
		self._center_column = None
		self._center_status_card = None
		self._encoder_card = None
		self._bottom_panel = None
		self._responsive_fonts = []
		self._responsive_heights = []

	def build(self):
		self._setup_display_mode()
		royal_blue = (0.26, 0.43, 0.93, 1)
		royal_blue_dark = (0.18, 0.31, 0.72, 1)
		royal_blue_soft = (0.36, 0.5, 0.94, 1)
		grey_bg = (0.11, 0.13, 0.17, 1)
		grey_card = (0.16, 0.19, 0.24, 1)
		grey_panel = (0.2, 0.23, 0.29, 1)
		grey_panel_alt = (0.23, 0.27, 0.34, 1)
		text_primary = (0.93, 0.95, 0.99, 1)
		text_secondary = (0.7, 0.77, 0.89, 1)

		Window.softinput_mode = "below_target"
		Window.clearcolor = grey_bg
		root = FloatLayout()
		viewport = FixedAspectFrame(aspect_ratio=self._target_ratio, size_hint=(1, 1))
		root.add_widget(viewport)

		with root.canvas.before:
			Color(*grey_bg)
			bg = Rectangle(pos=root.pos, size=root.size)
		root.bind(pos=lambda _i, _v: setattr(bg, "pos", root.pos))
		root.bind(size=lambda _i, _v: setattr(bg, "size", root.size))

		surface = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10), size_hint=(1, 1))
		viewport.set_content(surface)
		self._surface = surface

		top_bar = Card(orientation="horizontal", size_hint_y=0.1, padding=(dp(12), dp(8)), bg_color=grey_panel, border_color=(0.39, 0.53, 0.9, 0.5))
		self._top_bar = top_bar
		title_label = Label(text="NEXUS DRIVE", bold=True, color=text_primary, font_size=sp(16), halign="left", valign="middle")
		subtitle_label = Label(text="Landscape Control Interface", color=text_secondary, font_size=sp(12), halign="right", valign="middle")
		top_bar.add_widget(title_label)
		top_bar.add_widget(subtitle_label)
		for child in top_bar.children:
			if isinstance(child, Label):
				child.bind(size=child.setter("text_size"))
		self._register_responsive_font(title_label, 16)
		self._register_responsive_font(subtitle_label, 12)
		surface.add_widget(top_bar)

		main_row = BoxLayout(orientation="horizontal", spacing=dp(10), size_hint_y=0.62)
		self._main_row = main_row
		surface.add_widget(main_row)

		left_controls = Card(
			orientation="vertical",
			spacing=dp(10),
			padding=dp(10),
			size_hint_x=0.35,
			bg_color=grey_card,
		)
		main_row.add_widget(left_controls)

		throttle_pad = Card(orientation="vertical", spacing=dp(6), padding=dp(8), bg_color=grey_panel_alt)
		throttle_label = Label(text="Throttle (Up / Down)", bold=True, size_hint_y=None, height=dp(28), color=text_primary, font_size=sp(14))
		throttle_pad.add_widget(throttle_label)
		self._register_responsive_font(throttle_label, 14)
		self._register_responsive_height(throttle_label, 28)
		left_joystick = Joystick(
			on_move=self.on_joystick_move_vertical,
			on_release=self.on_joystick_release_vertical,
			axis="vertical",
			base_color=royal_blue_soft,
			knob_color=(0.93, 0.95, 0.99, 1),
		)
		throttle_pad.add_widget(left_joystick)
		left_controls.add_widget(throttle_pad)

		center_column = BoxLayout(orientation="vertical", spacing=dp(10), size_hint_x=0.3)
		self._center_column = center_column
		main_row.add_widget(center_column)

		center_status = Card(
			orientation="vertical",
			spacing=dp(10),
			padding=dp(12),
			size_hint=(1, 0.64),
			bg_color=royal_blue_dark,
			border_color=(0.72, 0.8, 1, 0.45),
		)
		self._center_status_card = center_status
		live_command_label = Label(text="Live Command", bold=True, size_hint_y=None, height=dp(30), color=text_primary, font_size=sp(15))
		center_status.add_widget(live_command_label)
		self._register_responsive_font(live_command_label, 15)
		self._register_responsive_height(live_command_label, 30)
		self._cmd_status = Label(
			text="(IDLE - waiting for command)",
			halign="center",
			valign="middle",
			color=text_primary,
			font_size=sp(14),
		)
		self._cmd_status.bind(size=self._cmd_status.setter("text_size"))
		self._register_responsive_font(self._cmd_status, 14)
		center_status.add_widget(self._cmd_status)
		format_label = Label(text="Format: (ESP command - meaning)", halign="center", valign="middle", color=(0.84, 0.89, 0.98, 1), font_size=sp(11), size_hint_y=None, height=dp(24))
		center_status.add_widget(format_label)
		self._register_responsive_font(format_label, 11)
		self._register_responsive_height(format_label, 24)
		center_column.add_widget(center_status)

		encoder_card = Card(
			orientation="vertical",
			spacing=dp(6),
			padding=dp(10),
			size_hint=(1, 0.36),
			bg_color=grey_panel_alt,
			border_color=(0.57, 0.68, 0.95, 0.5),
		)
		self._encoder_card = encoder_card
		encoder_title = Label(text="Encoder", bold=True, size_hint_y=None, height=dp(24), color=text_primary, font_size=sp(13))
		encoder_card.add_widget(encoder_title)
		self._register_responsive_font(encoder_title, 13)
		self._register_responsive_height(encoder_title, 24)
		self._encoder_status = Label(
			text="(E,0,0 - Left: 0 Right: 0)",
			halign="center",
			valign="middle",
			color=text_secondary,
			font_size=sp(12),
		)
		self._encoder_status.bind(size=self._encoder_status.setter("text_size"))
		self._register_responsive_font(self._encoder_status, 12)
		encoder_card.add_widget(self._encoder_status)
		center_column.add_widget(encoder_card)

		right_controls = Card(
			orientation="vertical",
			spacing=dp(10),
			padding=dp(10),
			size_hint_x=0.35,
			bg_color=grey_card,
		)
		main_row.add_widget(right_controls)

		steer_pad = Card(orientation="vertical", spacing=dp(6), padding=dp(8), bg_color=grey_panel_alt)
		steering_label = Label(text="Steering (Left / Right)", bold=True, size_hint_y=None, height=dp(28), color=text_primary, font_size=sp(14))
		steer_pad.add_widget(steering_label)
		self._register_responsive_font(steering_label, 14)
		self._register_responsive_height(steering_label, 28)
		right_joystick = Joystick(
			on_move=self.on_joystick_move_horizontal,
			on_release=self.on_joystick_release_horizontal,
			axis="horizontal",
			base_color=royal_blue_soft,
			knob_color=(0.93, 0.95, 0.99, 1),
		)
		steer_pad.add_widget(right_joystick)
		right_controls.add_widget(steer_pad)

		bottom_panel = Card(orientation="vertical", spacing=dp(8), size_hint_y=0.28, padding=dp(10), bg_color=grey_card)
		self._bottom_panel = bottom_panel
		self._status = Label(text="Waiting for ESP connection...", halign="left", valign="middle", color=text_secondary, font_size=sp(14), size_hint_y=None, height=dp(28))
		self._status.bind(size=self._status.setter("text_size"))
		self._register_responsive_font(self._status, 14)
		self._register_responsive_height(self._status, 28)
		bottom_panel.add_widget(self._status)

		primary_row = BoxLayout(orientation="horizontal", spacing=dp(8), size_hint_y=0.52)
		for label, command, color in [
			("Kick L", "K1", royal_blue),
			("RESET", "X", royal_blue_dark),
			("Kick R", "K2", royal_blue),
		]:
			btn = Button(text=label, bold=True, font_size=sp(13), background_normal="", background_color=color)
			self._register_responsive_font(btn, 13)
			btn.command = command
			btn.bind(on_release=self.on_extra)
			primary_row.add_widget(btn)
		bottom_panel.add_widget(primary_row)

		aux_row = GridLayout(cols=4, spacing=dp(8), size_hint_y=0.48)
		for label, command in [("B1", "B1"), ("B2", "B2"), ("B3", "B3"), ("B4", "B4")]:
			btn = Button(text=label, bold=True, font_size=sp(12), background_normal="", background_color=grey_panel)
			self._register_responsive_font(btn, 12)
			btn.command = command
			btn.bind(on_release=self.on_extra)
			aux_row.add_widget(btn)
		bottom_panel.add_widget(aux_row)
		surface.add_widget(bottom_panel)

		surface.bind(size=self._apply_responsive_layout)
		Clock.schedule_once(self._apply_responsive_layout, 0)

		return root

	def _register_responsive_font(self, widget, base_size):
		self._responsive_fonts.append((widget, base_size))

	def _register_responsive_height(self, widget, base_height):
		self._responsive_heights.append((widget, base_height))

	def _apply_responsive_layout(self, *_args):
		if not self._surface:
			return
		width, height = self._surface.size
		if width <= 0 or height <= 0:
			return

		scale = min(width / 1280.0, height / 720.0)
		compact_scale = min(max(scale, 0.78), 1.08)
		spacing_scale = min(max(scale, 0.82), 1.0)

		self._surface.padding = [dp(12 * spacing_scale)] * 4
		self._surface.spacing = dp(10 * spacing_scale)
		if self._top_bar:
			self._top_bar.padding = (dp(12 * spacing_scale), dp(8 * spacing_scale))
		if self._main_row:
			self._main_row.spacing = dp(10 * spacing_scale)
		if self._center_column:
			self._center_column.spacing = dp(10 * spacing_scale)
		if self._bottom_panel:
			self._bottom_panel.spacing = dp(8 * spacing_scale)
			self._bottom_panel.padding = [dp(10 * spacing_scale)] * 4

		if height < 620:
			if self._top_bar:
				self._top_bar.size_hint_y = 0.09
			if self._main_row:
				self._main_row.size_hint_y = 0.64
			if self._bottom_panel:
				self._bottom_panel.size_hint_y = 0.27
			if self._center_status_card:
				self._center_status_card.size_hint = (1, 0.6)
			if self._encoder_card:
				self._encoder_card.size_hint = (1, 0.4)
		else:
			if self._top_bar:
				self._top_bar.size_hint_y = 0.1
			if self._main_row:
				self._main_row.size_hint_y = 0.62
			if self._bottom_panel:
				self._bottom_panel.size_hint_y = 0.28
			if self._center_status_card:
				self._center_status_card.size_hint = (1, 0.64)
			if self._encoder_card:
				self._encoder_card.size_hint = (1, 0.36)

		for widget, base_size in self._responsive_fonts:
			widget.font_size = sp(base_size * compact_scale)

		for widget, base_height in self._responsive_heights:
			widget.height = dp(base_height * compact_scale)

	def on_start(self):
		self._schedule_connect_attempt()
		Clock.schedule_interval(self._auto_connect_tick, 1.5)

	def _setup_display_mode(self):
		if platform == "android":
			try:
				from jnius import autoclass
				PythonActivity = autoclass("org.kivy.android.PythonActivity")
				ActivityInfo = autoclass("android.content.pm.ActivityInfo")
				activity = PythonActivity.mActivity
				activity.setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE)
			except Exception:
				pass
		else:
			Window.minimum_width = 960
			Window.minimum_height = 540
			if Window.width / max(Window.height, 1) != self._target_ratio:
				Window.size = (1280, 720)

	def _auto_connect_tick(self, _dt):
		self._schedule_connect_attempt()

	def _schedule_connect_attempt(self):
		if self.sock or self._connecting:
			return
		self._connecting = True
		self._set_status(f"Connecting to {self._esp_ip}:{self._esp_port}...")
		threading.Thread(target=self._connect_worker, daemon=True).start()

	def on_extra(self, instance):
		command = getattr(instance, "command", instance.text.strip().upper())
		self.send_command(command)

	def on_joystick_move_vertical(self, _dx, dy, magnitude):
		if magnitude < self._axis_deadzone:
			self._update_axis("y", 0)
			return
		value = self._scale_axis_signed(dy, magnitude)
		self._update_axis("y", value)

	def on_joystick_release_vertical(self):
		self._update_axis("y", 0)

	def on_joystick_move_horizontal(self, dx, _dy, magnitude):
		if magnitude < self._axis_deadzone:
			self._update_axis("x", 0)
			return
		value = self._scale_axis_signed(dx, magnitude)
		self._update_axis("x", value)

	def on_joystick_release_horizontal(self):
		self._update_axis("x", 0)

	def _update_axis(self, axis, value):
		if axis == "x":
			if value == self._joy_x:
				return
			self._joy_x = value
		else:
			if value == self._joy_y:
				return
			self._joy_y = value
		self._dispatch_drive_command()

	def _scale_axis_signed(self, component, magnitude):
		if magnitude < self._axis_deadzone:
			return 0
		effective = (min(max(magnitude, self._axis_deadzone), 1.0) - self._axis_deadzone) / (1.0 - self._axis_deadzone)
		val = 55 + int(round(effective * 200))
		val = min(max(val, 55), 255)
		return val if component >= 0 else -val

	def _dispatch_drive_command(self):
		if not self.sock:
			return
		command = f"J{self._joy_x},{self._joy_y}"
		if command == self._last_drive_cmd:
			return
		self._last_drive_cmd = command
		self.send_commands([command], show_sent=False)

	def send_command(self, command):
		self.send_commands([command])

	def send_commands(self, commands, show_sent=True):
		if not commands:
			return
		self._set_command_status(commands[-1])
		if not self.sock:
			self._set_status(f"Not connected: {' | '.join(commands)}")
			return
		try:
			payload = "\n".join(commands) + "\n"
			self.sock.sendall(payload.encode("ascii"))
			if show_sent:
				self._set_status(f"Sent: {' | '.join(commands)}")
		except OSError:
			self._set_status("Connection lost")
			self._close_socket()

	def _set_command_status(self, command):
		if self._cmd_status:
			meaning = self._command_meaning(command)
			self._cmd_status.text = f"({command} - {meaning})"

	def _command_meaning(self, command):
		if command.startswith("J"):
			payload = command[1:]
			parts = payload.split(",", 1)
			if len(parts) == 2:
				try:
					x_val = int(parts[0])
					y_val = int(parts[1])
				except ValueError:
					return "Drive malformed"
				steer = "Right" if x_val > 0 else "Left" if x_val < 0 else "Center"
				throttle = "Forward" if y_val > 0 else "Reverse" if y_val < 0 else "Stop"
				return f"Drive {throttle} Y={y_val}, steer {steer} X={x_val}"
			return "Drive malformed"

		mapping = {
			"K1": "Kick left servo",
			"K2": "Kick right servo",
			"B1": "Aux button 1",
			"B2": "Aux button 2",
			"B3": "Aux button 3",
			"B4": "Aux button 4",
			"X": "Emergency reset/stop",
		}
		return mapping.get(command, "Unknown command")

	def _connect_worker(self):
		try:
			sock = socket.create_connection((self._esp_ip, self._esp_port), timeout=2)
			sock.settimeout(0.35)
		except OSError:
			self._set_status_threadsafe("Waiting for ESP AP...")
			self._connecting = False
			return

		self._close_socket()
		self.sock = sock
		self._set_status_threadsafe(f"Connected to {self._esp_ip}:{self._esp_port}")
		self._start_socket_reader()
		self._connecting = False

	def _start_socket_reader(self):
		if self._reader_running:
			return
		self._reader_running = True
		threading.Thread(target=self._socket_reader_worker, daemon=True).start()

	def _socket_reader_worker(self):
		buffer = ""
		while self.sock:
			try:
				chunk = self.sock.recv(256)
				if not chunk:
					break
				buffer += chunk.decode("ascii", errors="ignore")
				while "\n" in buffer:
					line, buffer = buffer.split("\n", 1)
					self._handle_incoming_line(line.strip())
			except socket.timeout:
				continue
			except OSError:
				break

		self._reader_running = False
		if self.sock:
			self._set_status_threadsafe("Connection lost")
			Clock.schedule_once(lambda _dt: self._close_socket())

	def _handle_incoming_line(self, line):
		if not line:
			return
		if line.startswith("E,"):
			parts = line.split(",", 2)
			if len(parts) != 3:
				return
			try:
				left = int(parts[1])
				right = int(parts[2])
			except ValueError:
				return
			Clock.schedule_once(lambda _dt: self._update_encoder_status(left, right))

	def _update_encoder_status(self, left, right):
		if self._encoder_status:
			self._encoder_status.text = f"(E,{left},{right} - Left: {left} Right: {right})"

	def _set_status(self, message):
		if self._status:
			self._status.text = message
			lower = message.lower()
			if "connected" in lower or "found" in lower or "sent" in lower:
				self._status.color = (0.71, 0.8, 0.97, 1)
			elif "scan" in lower or "trying" in lower or "connecting" in lower:
				self._status.color = (0.84, 0.89, 0.98, 1)
			else:
				self._status.color = (0.73, 0.79, 0.9, 1)

	def _set_status_threadsafe(self, message):
		Clock.schedule_once(lambda _dt: self._set_status(message))

	def _close_socket(self):
		self._last_drive_cmd = None
		self._reader_running = False
		if self.sock:
			try:
				self.sock.close()
			except OSError:
				pass
			self.sock = None
		self._connecting = False


if __name__ == "__main__":
	RcCarControllerApp().run()
