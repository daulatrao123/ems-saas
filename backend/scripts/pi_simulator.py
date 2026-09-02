#!/usr/bin/env python3
import requests, time, uuid

BACKEND_URL = "https://ems-saass.onrender.com"
DEVICE_ID = "d9e8206b-a9f3-4294-91f8-df66d65bf417"
API_KEY = "b68c8b3b-3633-4c77-a36e-478bff1e7430" # Use your newly rotated key here

def simulate_pi():
    print(f"Starting Pi Simulator for Device {DEVICE_ID}...")
    session = requests.Session()
    
    pi_state = {
        "deviceId": DEVICE_ID,
        "key": API_KEY,
        "firmwareVersion": "5.0.0-SIMULATOR",
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
            "A": {"usedDays": 1, "targetDays": 10, "physicalToggle": "ON", "clicks": 5},
            "B": {"usedDays": 2, "targetDays": 12, "physicalToggle": "ON", "clicks": 3},
            "G": {"usedDays": 0, "targetDays": 10, "physicalToggle": "OFF", "clicks": 0}
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
            res = session.post(f"{BACKEND_URL}/api/pi/sync", json=pi_state, timeout=15)
            if res.status_code != 200:
                print(f"❌ Sync Failed: {res.status_code} - {res.text}")
                time.sleep(15)
                continue
                
            data = res.json()
            print("✅ Sync Successful. last_sync updated.")
            
            # Clear events after successful sync so we don't resend them
            pi_state["events"].clear()
            
            cmd = data.get("command")
            cmd_id = data.get("command_id")
            
            if cmd and cmd_id:
                print(f"📦 Received Command: {cmd} (ID: {cmd_id})")
                event_msg = ""
                
                if cmd == "set_active_wing":
                    wing = data.get("wing")
                    print(f"⚡ Executing: Activating Wing {wing}")
                    pi_state["activeWing"] = wing
                    event_msg = f"Command: Activated Wing {wing}"
                elif cmd == "off_all":
                    print("⚡ Executing: Turning off all wings")
                    pi_state["activeWing"] = None
                    event_msg = "Command: All wings turned OFF"
                elif cmd == "reset_days":
                    print("⚡ Executing: Resetting days")
                    for w in pi_state["wings"]: pi_state["wings"][w]["usedDays"] = 0
                    event_msg = "System: Monthly reset executed"
                elif cmd == "set_days":
                    wing = data.get("wing")
                    days = data.get("params", {}).get("days")
                    print(f"⚡ Executing: Setting Wing {wing} target to {days} days")
                    # PRODUCTION FIX: Actually update the simulator's local state
                    if wing in pi_state["wings"]:
                        pi_state["wings"][wing]["targetDays"] = days
                    event_msg = f"Command: Set Wing {wing} target to {days} days"
                    
                # Generate a new event for the executed command
                if event_msg:
                    pi_state["events"].append({
                        "eventId": str(uuid.uuid4()),
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "type": "command" if "Command" in event_msg else "system",
                        "message": event_msg
                    })
                    
                ack_payload = {
                    "deviceId": DEVICE_ID,
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