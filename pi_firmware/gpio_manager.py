# pi_firmware/gpio_manager.py
from gpiozero import OutputDevice, Button

class GpioManager:
    def __init__(self, config, logger):
        self.log = logger
        self.relays = {}
        self.toggles = {}

        # PRODUCTION v6.0: Initialize 4 slots
        slot_pins = config.slots
        for slot_code, pins in slot_pins.items():
            try:
                self.relays[slot_code] = OutputDevice(pins["relay"], active_high=True, initial_value=False)
                self.toggles[slot_code] = Button(pins["toggle"], pull_up=True, bounce_time=0.05)
            except Exception as e:
                self.log.error(f"GPIO init failed for slot {slot_code}: {e}")

    def get_physical_toggle(self, slot_code):
        if slot_code not in self.toggles: return "UNKNOWN"
        try:
            return "ON" if self.toggles[slot_code].is_active else "OFF"
        except:
            return "UNKNOWN"

    def verify_relay_state(self, slot_code):
        if slot_code not in self.relays: return False
        try:
            return self.relays[slot_code].is_active
        except:
            return False

    def set_active_slot(self, slot_code):
        # PRODUCTION FIX: Safe Break-Before-Make
        if slot_code not in self.relays:
            self.log.error(f"Cannot activate {slot_code}: relay not initialized")
            return False
            
        self.relays[slot_code].on()
        
        if not self.verify_relay_state(slot_code):
            self.log.error(f"Hardware verification failed: {slot_code} did not turn ON")
            self.relays[slot_code].off() 
            return False
            
        for sc, r in self.relays.items():
            if sc != slot_code and r.is_active:
                r.off()
                
        return True

    def off_slot(self, slot_code):
        if slot_code in self.relays: self.relays[slot_code].off()

    def off_all(self):
        for r in self.relays.values(): r.off()

    def cleanup(self):
        try: self.off_all()
        except: pass