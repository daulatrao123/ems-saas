#!/usr/bin/env python3
"""
Pi Simulator: Used to validate the Render/Neon backend chain
without physical Pi hardware.
"""
import requests
import time
import uuid

# --- CONFIGURATION ---
BACKEND_URL = "https://ems-saass.onrender.com"
SOCIETY_ID = 1
API_KEY = "YOUR_ACTUAL_API_KEY_HERE" # Replace with the key from your DB

def simulate_pi():
    print(f"Starting Pi Simulator for Society {SOCIETY_ID}...")
    session = requests.Session()
    
    # Simulated Pi State (STRICT: Only runtime data, no name/display_name/target_days/disabled)
    pi_state = {
        "societyId": SOCIETY_ID,
        "key": API_KEY,
        "firmwareVersion": "3.3.0-SIMULATOR",
        "activeWing": "A",
        "resetDay": 15,
        "emergencyStop": False,
        "uptimeSeconds": 0,
        "cpuTemp": 45.0,
        "diskFreeMB": 8000,
        "bootCount": 1,
        "lastShutdownReason": "SIMULATOR_BOOT",
        "clockSource": "NTP",
        "watchdogEnabled": True,
        "lastRebootReason": "SIMULATOR_BOOT",
        "wings": {
            "A": {"usedDays": 1, "physicalToggle": "ON", "clicks": 5},
            "B": {"usedDays": 2, "physicalToggle": "ON", "clicks": 3},
            "G": {"usedDays": 0, "physicalToggle": "OFF", "clicks": 0}
        },
        "events": [{
            "eventId": str(uuid.uuid4()),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "type": "system",
            "message": "Simulator booted"
        }]
    }

    while True:
        pi_state["uptimeSeconds"] += 60
        pi_state["cpuTemp"] = 45.0 + (time.time() % 10)
        
        print("\n--- Syncing with Backend ---")
        try:
            # 1. Sync State
            res = session.post(f"{BACKEND_URL}/api/pi/sync", json=pi_state, timeout=15)
            if res.status_code != 200:
                print(f"❌ Sync Failed: {res.status_code} - {res.text}")
                time.sleep(15)
                continue
                
            data = res.json()
            print("✅ Sync Successful. last_sync updated.")
            
            # 2. Check for Commands
            cmd = data.get("command")
            cmd_id = data.get("command_id")
            
            if cmd and cmd_id:
                print(f"📦 Received Command: {cmd} (ID: {cmd_id})")
                
                # Simulate Execution
                if cmd == "set_active_wing":
                    wing = data.get("wing")
                    print(f"⚡ Executing: Activating Wing {wing}")
                    pi_state["activeWing"] = wing
                elif cmd == "off_all":
                    print("⚡ Executing: Turning off all wings")
                    pi_state["activeWing"] = None
                    
                # 3. Send ACK
                ack_payload = {
                    "societyId": SOCIETY_ID,
                    "key": API_KEY,
                    "command_id": cmd_id,
                    "success": True,
                    "error": None,
                    "result": f"Executed {cmd}"
                }
                ack_res = session.post(f"{BACKEND_URL}/api/pi/command-ack", json=ack_payload, timeout=10)
                if ack_res.status_code == 200:
                    print("✅ ACK Sent Successfully.")
                else:
                    print(f"❌ ACK Failed: {ack_res.status_code} - {ack_res.text}")
            else:
                print("No pending commands.")
                
        except Exception as e:
            print(f"❌ Network Error: {e}")
            
        print("Sleeping for 60 seconds...")
        time.sleep(60)

if __name__ == "__main__":
    simulate_pi()