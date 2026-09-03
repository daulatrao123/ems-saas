#!/usr/bin/env python3
import requests, time, uuid, threading, random

# --- CONFIGURATION ---
BACKEND_URL = "https://ems-saass.onrender.com"

# 10 Auto-Generated Devices
DEVICES = [
    {"id": "0a5534cb-aa87-49bc-9ae0-915ed99f24ba", "key": "1752adf4-cebd-4d6e-9551-e754c4ebd175"},
    {"id": "0b947839-a71a-40e7-a3ba-6ca6af11cbb8", "key": "9ab2092d-8e84-4efa-a8a9-8a50ef3af057"},
    {"id": "c49e481a-9cc9-4370-bf2a-4aaf66cd370a", "key": "b0f50c3b-a144-441d-a557-b3733e3a7238"},
    {"id": "d5c0db1e-180b-4a92-ba87-3af3946f7756", "key": "21c13e16-908f-4010-9e41-fffb6d5caffd"},
    {"id": "20934370-8af3-4a0c-a9fe-51144369ffa6", "key": "1bea1aa5-b3ec-48b7-a698-93e20726d0b9"},
    {"id": "81e730cb-8903-45ba-8087-5fd958b592f6", "key": "452ba77a-b0d4-4117-95c4-98d4aa78c2cb"},
    {"id": "4add2d8a-32a9-415d-b988-eafde8a536bb", "key": "d8b7f04f-ad9e-432e-bd30-a876c8ca3793"},
    {"id": "dbdb6c06-e0ca-4290-af93-d4ec3f178fa0", "key": "de95299c-6db6-4861-b414-a4f98cee2d1c"},
    {"id": "d008c020-4382-4b8d-82a3-147482e008bb", "key": "520d8ea4-d666-4f24-a0dc-8dba4a0276f6"},
    {"id": "635496ff-13bd-4717-a003-8e89e4056c0c", "key": "175d7695-1746-455d-a827-48b52184d844"}
]

def simulate_pi(device_id, api_key):
    print(f"[{device_id[:8]}] Starting Simulator...")
    session = requests.Session()
    
    pi_state = {
        "deviceId": device_id,
        "key": api_key,
        "firmwareVersion": "5.3.1-sim",
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
            "A": {"usedDays": random.randint(1, 5), "physicalToggle": "ON", "clicks": 5},
            "B": {"usedDays": random.randint(1, 5), "physicalToggle": "ON", "clicks": 3},
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
        
        print(f"[{device_id[:8]}] Syncing...")
        try:
            res = session.post(f"{BACKEND_URL}/api/pi/sync", json=pi_state, timeout=15)
            if res.status_code != 200:
                print(f"[{device_id[:8]}] ❌ Sync Failed: {res.status_code} - {res.text[:100]}")
                time.sleep(15)
                continue
                
            data = res.json()
            print(f"[{device_id[:8]}] ✅ Sync OK")
            
            pi_state["events"].clear()
            
            cmd = data.get("command")
            cmd_id = data.get("command_id")
            
            if cmd and cmd_id:
                print(f"[{device_id[:8]}] 📦 Command: {cmd}")
                event_msg = ""
                success = True
                
                if cmd == "set_active_wing":
                    wing = data.get("wing")
                    if wing in pi_state["wings"]:
                        pi_state["activeWing"] = wing
                        event_msg = f"Activated {wing}"
                elif cmd == "off_all":
                    pi_state["activeWing"] = None
                    event_msg = "All wings OFF"
                elif cmd == "set_days":
                    wing = data.get("wing")
                    days = data.get("params", {}).get("days")
                    if wing in pi_state["wings"]:
                        event_msg = f"Set {wing} to {days} days"
                else:
                    event_msg = f"Executed {cmd}"
                    
                if event_msg:
                    pi_state["events"].append({
                        "eventId": str(uuid.uuid4()),
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "type": "command",
                        "message": event_msg
                    })
                    
                ack_payload = {
                    "deviceId": device_id, "key": api_key,
                    "command_id": cmd_id, "success": success,
                    "error": None, "result": "Executed"
                }
                session.post(f"{BACKEND_URL}/api/pi/command-ack", json=ack_payload, timeout=10)
                print(f"[{device_id[:8]}] ✅ ACK Sent")
                
        except Exception as e:
            print(f"[{device_id[:8]}] ❌ Error: {e}")
            
        # Add jitter (1-10s) so 10 Pis don't hit the DB at the exact same time
        sleep_time = 60 + random.randint(1, 10)
        time.sleep(sleep_time)

if __name__ == "__main__":
    threads = []
    for device in DEVICES:
        t = threading.Thread(target=simulate_pi, args=(device["id"], device["key"]), daemon=True)
        threads.append(t)
        t.start()
            
    print("10-Device Simulator running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down simulators...")