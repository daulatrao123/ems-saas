import subprocess

VALID_WINGS = ("A", "B", "G")

class CommandHandler:
    def __init__(self, config, state, gpio, lcd, logger, api_client, offline_queue):
        self.cfg = config
        self.state = state
        self.gpio = gpio
        self.lcd = lcd
        self.log = logger
        self.api = api_client
        self.db = offline_queue

    def execute(self, cmd):
        cid = cmd.get("command_id")
        ctype = cmd.get("command")
        wing = cmd.get("wing")
        params = cmd.get("params") or {}
        
        # 1. IDEMPOTENCY CHECK
        existing = self.db.check_command_executed(cid)
        if existing:
            if existing["status"] == "SUCCESS":
                self.log.info(f"Command {cid} already executed successfully. Skipping physical execution.")
                return cid, True, existing["result"], None
            elif existing["status"] == "STARTED":
                self.log.warning(f"Command {cid} was interrupted mid-execution. Aborting to prevent duplicate physical action.")
                return cid, False, None, "Interrupted mid-execution"

        # 2. LOG START (Before physical side effect)
        self.db.log_command_start(cid)
        
        try:
            # 3. EXECUTE PHYSICAL ACTION
            success, result = self._dispatch(ctype, wing, params, cid)
            
            # 4. VERIFY HARDWARE STATE
            if success and ctype in ["set_active_wing", "off_wing", "off_all"]:
                if not self._verify_hardware(ctype, wing):
                    self.log.error(f"Hardware verification failed for command {cid}")
                    self.db.update_command_status(cid, "FAILED", "Hardware verification failed")
                    return cid, False, None, "Hardware verification failed"
            
            # 5. LOG SUCCESS
            self.db.update_command_status(cid, "SUCCESS", result)
            return cid, True, result, None
            
        except Exception as e:
            self.log.exception(f"command {ctype} crashed: {e}")
            self.db.update_command_status(cid, "FAILED", str(e))
            return cid, False, None, str(e)

    def _dispatch(self, ctype, wing, p, cid):
        if ctype == "set_active_wing":
            w = (wing or "").upper()
            if w not in VALID_WINGS: return False, "invalid wing"
            if not self._wing_visible(w): return False, "wing not visible"
            if not self.state.set_active_wing(w): return False, "emergency stop active"
            self.gpio.set_active_wing(w)
            self.state.add_event("CMD", f"Wing {w} activated")
            return True, "active"

        if ctype == "set_days":
            return True, "days_set"

        if ctype == "set_reset_day":
            d = int(p.get("day", 0))
            if not (1 <= d <= 28): return False, "reset day out of range"
            self.state.set_reset_day(d)
            return True, f"reset_day={d}"

        if ctype == "off_wing":
            w = (wing or "").upper()
            if w not in VALID_WINGS: return False, "invalid wing"
            self.state.off_wing(w)
            self.gpio.off_wing(w)
            return True, f"{w} off"

        if ctype == "off_all":
            self.state.off_all()
            self.gpio.off_all()
            return True, "all off"

        if ctype == "reset_days":
            self.state.reset_days()
            return True, "days reset"

        if ctype == "restart":
            self.state.prepare_for_shutdown("EMS_RESTART")
            self.db.save_ack(cid, True, "restarting", None)
            self.lcd.show("Restarting EMS", "please wait...")
            subprocess.Popen(["systemctl", "restart", self.cfg.serviceName])
            return True, "restarting"

        if ctype == "reboot":
            self.state.prepare_for_shutdown("PI_REBOOT")
            self.db.save_ack(cid, True, "rebooting", None)
            self.gpio.cleanup()
            self.lcd.show("Rebooting Pi", "please wait...")
            subprocess.Popen(["shutdown", "-r", "now"])
            return True, "rebooting"

        if ctype == "lcd_display":
            l1 = (p.get("line1") or "")[:16]
            l2 = (p.get("line2") or "")[:16]
            if not l1 and not l2: return False, "empty lcd msg"
            dur = max(1, min(120, int(p.get("duration", 5))))
            self.lcd.flash_async(l1, l2, dur)
            return True, f"lcd {dur}s"

        return False, f"unknown command {ctype}"

    def _verify_hardware(self, ctype, wing):
        """Reads back the actual GPIO state to verify the physical side effect."""
        if ctype == "set_active_wing":
            return self.gpio.verify_relay_state(wing)
        elif ctype == "off_wing":
            return not self.gpio.verify_relay_state(wing)
        elif ctype == "off_all":
            return not any(self.gpio.verify_relay_state(w) for w in VALID_WINGS)
        return True

    def _wing_visible(self, w):
        wing = self.state.data["wings"][w]
        return wing["targetDays"] > 0 and wing["physicalToggle"] == "ON" and not wing["disabled"]