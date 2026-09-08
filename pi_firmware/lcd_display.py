"""EMS 20x4 I2C LCD (PCF8574 @ 0x27, bus 1) — DISPLAY ONLY.

Hard isolation: this module imports no hardware, queue or state module and holds no reference
to the FSM or to anything that can switch a load. It receives an already-built, read-only view dict
from the controller (`view_provider()`) and renders text. Any LCD failure is caught, logged and the
display simply goes dark; the controller never sees an exception and systemd never restarts for it.
"""
import textwrap
import threading
import time
from datetime import datetime, timezone

LCD_COLS = 20
LCD_ROWS = 4
LCD_I2C_BUS = 1
LCD_I2C_ADDRESS = 0x27
LCD_ROTATION_INTERVAL_S = 5.0
LCD_REINIT_INTERVAL_S = 60.0
LCD_MESSAGE_MAX_CHARS = 80


def _fit(text, width=LCD_COLS):
    text = "" if text is None else str(text)
    return text[:width].ljust(width)


def _lr(left, right, width=LCD_COLS):
    left, right = str(left), str(right)
    gap = width - len(left) - len(right)
    return _fit(left + " " * max(1, gap) + right)


def fmt_days(slot):
    """USED/ALLOTTED; missing values are shown as N/A, never as 0."""
    used, alloc = slot.get("used_days"), slot.get("target_days")
    if used is None and alloc is None:
        return "N/A"
    if alloc is None:
        return f"{used} USED, ALLOC N/A"
    if used is None:
        return f"USED N/A / {alloc}"
    return f"{used}/{alloc}"


def _onoff(value):
    if value is None:
        return "UNKNOWN"
    if isinstance(value, bool):
        return "ON" if value else "OFF"
    return str(value).upper()


def render_system(view, now=None):
    now = now or datetime.now()  # system local time (Pi timezone), deliberately not UTC
    return [
        _fit("EMS CONTROLLER"),
        _fit(f"SYSTEM: {str(view.get('system_state') or 'UNKNOWN').upper()}"),
        _fit(f"DATE: {now.strftime('%d-%m-%Y')}"),
        _fit(f"TIME: {now.strftime('%H:%M:%S')}"),
    ]


def render_fault(view):
    return [
        _fit("EMS CONTROLLER"),
        _fit("SYSTEM: FAULT"),
        _fit("HARDWARE FAULT" if view.get("hardware_fault") else "INTERLOCK FAULT"),
        _fit("RELAYS: SAFE"),
    ]


def _slot_line(code, slot, active):
    if slot.get("disabled", True):
        return f"{code} DISABLED"
    return f"{code} {'ON ' if active == code else 'OFF'} {fmt_days(slot)}"


def render_wings(view, pair):
    slots, active = view.get("slots", {}), view.get("active_slot")
    a, b = pair
    sa, sb = slots.get(a, {}), slots.get(b, {})
    # Three distinct facts per wing: relay/active state + days, contactor feedback, physical toggle.
    return [
        _fit(_slot_line(a, sa, active)),
        _fit(_slot_line(b, sb, active)),
        _lr(f"CT {a}:{_onoff(sa.get('contactor'))[:7]}", f"{b}:{_onoff(sb.get('contactor'))[:7]}"),
        _lr(f"TG {a}:{_onoff(sa.get('toggle'))[:7]}", f"{b}:{_onoff(sb.get('toggle'))[:7]}"),
    ]


def wrap_message(text, cols=LCD_COLS, rows=LCD_ROWS - 1):
    words = " ".join(str(text or "").split())
    lines = textwrap.wrap(words, width=cols, break_long_words=True, break_on_hyphens=False)[:rows]
    return [_fit(ln) for ln in lines] + [_fit("")] * (rows - len(lines))


def render_message(view):
    return [_fit("MESSAGE")] + wrap_message(view.get("message", {}).get("message"))


def message_is_live(msg, now_ts=None):
    """Active and not expired (expires_at ISO-8601 UTC or None). Invalid/oversized -> not shown."""
    if not msg or not str(msg.get("message", "")).strip() or not msg.get("active", True):
        return False
    if len(str(msg["message"])) > LCD_MESSAGE_MAX_CHARS:
        return False
    exp = msg.get("expires_at")
    if exp:
        try:
            exp_ts = datetime.fromisoformat(str(exp).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return False
        if (now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()) >= exp_ts:
            return False
    return True


def build_frames(view, now=None):
    """Rotation order. FAULT screen takes priority; MESSAGE only when a live message exists."""
    frames = []
    if str(view.get("system_state", "")).upper() == "FAULT":
        frames.append(render_fault(view))
    frames.append(render_system(view, now))
    frames.append(render_wings(view, ("A", "B")))
    frames.append(render_wings(view, ("C", "D")))
    if message_is_live(view.get("message")):
        frames.append(render_message(view))
    return frames


class LcdDisplay:
    """Background renderer. `view_provider` returns a plain dict; nothing here can act on hardware."""

    def __init__(self, view_provider, logger, rotation_interval_s=LCD_ROTATION_INTERVAL_S, driver_factory=None, enabled=True):
        self.view_provider = view_provider
        self.logger = logger
        self.rotation_interval_s = float(rotation_interval_s)
        self.driver_factory = driver_factory or self._default_driver
        self.enabled = enabled
        self.lcd = None
        self.available = False
        self.init_error = None
        self.frames_written = 0
        self._last_lines = None
        self._next_reinit = 0.0
        self._running = False
        self._thread = None
        self._stop = threading.Event()

    @staticmethod
    def _default_driver():
        from RPLCD.i2c import CharLCD  # imported lazily: absence must not break the controller
        return CharLCD(i2c_expander="PCF8574", address=LCD_I2C_ADDRESS, port=LCD_I2C_BUS,
                       cols=LCD_COLS, rows=LCD_ROWS, charmap="A00", auto_linebreaks=False)

    def _init_lcd(self):
        try:
            self.lcd = self.driver_factory()
            self.lcd.clear()
            self.available, self.init_error = True, None
            self.logger.info("LCD ready (I2C bus %s addr 0x%02X, %dx%d).", LCD_I2C_BUS, LCD_I2C_ADDRESS, LCD_COLS, LCD_ROWS)
        except Exception as exc:  # LCD is optional: log and carry on without it
            self.lcd, self.available, self.init_error = None, False, f"{type(exc).__name__}: {exc}"
            self._next_reinit = time.monotonic() + LCD_REINIT_INTERVAL_S
            self.logger.warning("LCD unavailable (%s); controller continues without display.", self.init_error)
        return self.available

    def start(self):
        if not self.enabled:
            return
        self._init_lcd()
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="EMS-LCD", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._stop.set()
        if self.lcd is not None:
            try:
                self.lcd.clear()
            except Exception:
                pass

    def write_lines(self, lines):
        """Write a 4-line frame; skips identical frames to spare the I2C bus. Never raises."""
        if lines == self._last_lines:
            return False
        if not self.available:
            if time.monotonic() >= self._next_reinit and not self._init_lcd():
                return False
        try:
            for row, text in enumerate(lines[:LCD_ROWS]):
                self.lcd.cursor_pos = (row, 0)
                self.lcd.write_string(_fit(text))
            self._last_lines = list(lines)
            self.frames_written += 1
            return True
        except Exception as exc:
            self.available = False
            self._last_lines = None
            self._next_reinit = time.monotonic() + LCD_REINIT_INTERVAL_S
            self.logger.warning("LCD write failed (%s); display paused, controller unaffected.", exc)
            return False

    def render_once(self, index, now=None):
        try:
            view = self.view_provider() or {}
            frames = build_frames(view, now)
        except Exception as exc:
            self.logger.warning("LCD view build failed: %s", exc)
            return 0
        if not frames:
            return 0
        self.write_lines(frames[index % len(frames)])
        return len(frames)

    def _loop(self):
        index = 0
        next_rotate = time.monotonic() + self.rotation_interval_s
        while self._running:
            # Re-render every second: identical frames are skipped, so only the clock line costs I2C writes.
            n = self.render_once(index)
            self._stop.wait(1.0)  # Event.wait: independent of time.sleep, never blocks the controller
            if time.monotonic() >= next_rotate:
                index = (index + 1) % max(1, n or 1)
                next_rotate = time.monotonic() + self.rotation_interval_s
