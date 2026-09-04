# pi_firmware/commands.py
import subprocess

VALID_SLOTS = ("A", "B", "C", "D")

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
        slot = cmd.get("slot") # Changed from wing to slot
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

        # 2. LOG START
        self.db.log_command_start(cid)
        
        try:
            # 3. EXECUTE & VERIFY
            success, result = self._dispatch(ctype, slot, params, cid)
            self.db.update_command_status(cid, "SUCCESS" if success else "FAILED", result)
            return cid, success, result, None if success else "Execution Failed"
        except Exception as e:
            self.log.exception(f"command {ctype} crashed: {e}")
            self.db.update_command_status(cid, "FAILED", str(e))
            return cid, False, None, str(e)

    def _dispatch(self, ctype, slot, p, cid):
        if ctype == "set_active_slot":
            s = (slot or "").upper()
            if s not in VALID_SLOTS: return False, "invalid slot"
            if not self._slot_visible(s): return False, "slot not visible"
            if not self.state.set_active_slot(s): return False, "emergency stop active"
            
            if not self.gpio.set_active_slot(s):
                self.state.off_slot(s) # Revert state since hardware failed
                return False, "hardware verification failed"
                
            self.state.add_event("CMD", f"Slot {s} activated")
            return True, "active"

        if ctype == "set_days":
            return True, "days_set"

        if ctype == "set_reset_day":
            d = int(p.get("day", 0))
            if not (1 <= d <= 28): return False, "reset day out of range"
            self.state.set_reset_day(d)
            return True, f"reset_day={d}"

        if ctype == "off_slot":
            s = (slot or "").upper()
            if s not in VALID_SLOTS: return False, "invalid slot"
            self.state.off_slot(s)
            self.gpio.off_slot(s)
            return True, f"{s} off"

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

    def _slot_visible(self, s):
        slot = self.state.data["slots"][s]
        return slot["targetDays"] > 0 and slot["physicalToggle"] == "ON" and not slot["disabled"]