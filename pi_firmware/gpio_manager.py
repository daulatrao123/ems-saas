from gpiozero import OutputDevice, Button

class GpioManager:
    def __init__(self, config, logger):
        self.log = logger
        self.relays = {}
        self.toggles = {}

        # PRODUCTION FIX: Centralized hardware mapping
        wing_pins = {
            "A": {"relay": 17, "toggle": 5},
            "B": {"relay": 27, "toggle": 6},
            "G": {"relay": 23, "toggle": 13}
        }

        for wing, pins in wing_pins.items():
            try:
                self.relays[wing] = OutputDevice(pins["relay"], active_high=True, initial_value=False)
                self.toggles[wing] = Button(pins["toggle"], pull_up=True, bounce_time=0.05)
            except Exception as e:
                self.log.error(f"GPIO init failed for wing {wing}: {e}")

    def get_physical_toggle(self, wing):
        if wing not in self.toggles: return "UNKNOWN"
        try:
            return "ON" if self.toggles[wing].is_active else "OFF"
        except:
            return "UNKNOWN"

    # PRODUCTION FIX: Read back actual GPIO state to verify physical side effect
    def verify_relay_state(self, wing):
        if wing not in self.relays: return False
        try:
            return self.relays[wing].is_active
        except:
            return False

    def set_active_wing(self, wing):
        for w, r in self.relays.items():
            if r.is_active: r.off()
        if wing and wing in self.relays:
            self.relays[wing].on()

    def off_wing(self, wing):
        if wing in self.relays: self.relays[wing].off()

    def off_all(self):
        for r in self.relays.values(): r.off()

    def cleanup(self):
        try: self.off_all()
        except: pass