#!/usr/bin/env python3
import requests, time, uuid, os

BACKEND_URL = os.environ.get("BACKEND_URL")
DEVICE_ID = os.environ.get("PI_DEVICE_ID")
API_KEY = os.environ.get("PI_API_KEY")

if not all([BACKEND_URL, DEVICE_ID, API_KEY]):
    print("❌ ERROR: BACKEND_URL, PI_DEVICE_ID, and PI_API_KEY environment variables are required.")
    exit(1)

def simulate_pi():
    print(f"Starting Pi Simulator for Device {DEVICE_ID}...")
    session = requests.Session()
    
    pi_state = {
        "deviceId": DEVICE_ID,
        "key": API_KEY,
        "firmwareVersion": "5.3.1-SIMULATOR",
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
                success = True
                error_msg = None
                
                if cmd == "set_active_wing":
                    wing = data.get("wing")
                    if wing in pi_state["wings"]:
                        pi_state["activeWing"] = wing
                        event_msg = f"Command: Activated Wing {wing}"
                    else:
                        success = False
                        error_msg = f"Invalid wing: {wing}"
                elif cmd == "off_all":
                    pi_state["activeWing"] = None
                    event_msg = "Command: All wings turned OFF"
                elif cmd == "reset_days":
                    for w in pi_state["wings"]: pi_state["wings"][w]["usedDays"] = 0
                    event_msg = "System: Monthly reset executed"
                elif cmd == "set_days":
                    wing = data.get("wing")
                    days = data.get("params", {}).get("days")
                    if wing in pi_state["wings"] and days is not None:
                        pi_state["wings"][wing]["targetDays"] = int(days)
                        event_msg = f"Command: Set Wing {wing} target to {days} days"
                    else:
                        success = False
                        error_msg = "Invalid wing or days parameter"
                elif cmd == "set_reset_day":
                    day = data.get("params", {}).get("day")
                    if day:
                        pi_state["resetDay"] = int(day)
                        event_msg = f"Command: Set reset day to {day}"
                    else:
                        success = False
                        error_msg = "Missing day parameter"
                elif cmd == "off_wing":
                    wing = data.get("wing")
                    if wing in pi_state["wings"] and pi_state["activeWing"] == wing:
                        pi_state["activeWing"] = None
                        event_msg = f"Command: Turned OFF Wing {wing}"
                    else:
                        event_msg = f"Command: Wing {wing} already OFF"
                elif cmd == "lcd_display":
                    l1 = data.get("params", {}).get("line1", "")
                    l2 = data.get("params", {}).get("line2", "")
                    event_msg = f"Command: LCD displayed '{l1} | {l2}'"
                elif cmd in ["restart", "reboot"]:
                    event_msg = f"Command: {cmd} initiated (simulated)"
                else:
                    success = False
                    error_msg = f"Unsupported command: {cmd}"
                    
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
                    "success": success,
                    "error": error_msg,
                    "result": "Executed" if success else "Failed"
                }
                ack_res = session.post(f"{BACKEND_URL}/api/pi/command-ack", json=ack_payload, timeout=10)
                if ack_res.status_code == 200:
                    print(f"{'✅ ACK Sent Successfully.' if success else '❌ ACK Sent (Failed).'}")
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