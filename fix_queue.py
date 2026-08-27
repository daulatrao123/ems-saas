fp = r'C:\Users\Daulat\solid-funcular\backend\main.py'
with open(fp, encoding='utf-8') as f:
    c = f.read()
n = 0

# 1) pi_sync: return oldest pending, mark as sent
o1 = '''    reply = {"success": True, "command": None}
    pending = db.get("pi_commands", {}).get(sid)
    if pending and pending.get("status") == "pending":
        reply["command"] = pending["command"]
        reply["command_id"] = pending["id"]
        if pending.get("wing"):
            reply["wing"] = pending["wing"]
        reply["params"] = pending.get("params", {})

    return reply'''
r1 = '''    reply = {"success": True, "command": None}
    cmds = db.get("pi_commands", {}).get(sid, [])
    if isinstance(cmds, dict): cmds = [cmds]; db.setdefault("pi_commands", {}); db["pi_commands"][sid] = cmds
    for cmd in cmds:
        if cmd.get("status") == "pending":
            reply["command"] = cmd["command"]
            reply["command_id"] = cmd["id"]
            if cmd.get("wing"): reply["wing"] = cmd["wing"]
            reply["params"] = cmd.get("params", {})
            cmd["status"] = "sent"
            cmd["sent_at"] = datetime.now(timezone.utc).isoformat()
            break
    save_db(db)
    return reply'''
if o1 in c: c = c.replace(o1, r1, 1); n += 1

# 2) command-ack: find by id in list
o2 = '''    pending = db.get("pi_commands", {}).get(sid)
    if not pending or pending.get("id") != str(command_id):
        raise HTTPException(404, "Command not found or already acknowledged")
    pending["acked_at"] = datetime.now(timezone.utc).isoformat()
    pending["status"] = "acknowledged" if success else "failed"
    pending["error"] = None if success else error
    pending["result"] = result
    save_db(db)
    return {"success": True, "status": pending["status"]}'''
r2 = '''    cmds = db.get("pi_commands", {}).get(sid, [])
    if isinstance(cmds, dict): cmds = [cmds]
    found = next((cmd for cmd in cmds if cmd.get("id") == str(command_id)), None)
    if not found: raise HTTPException(404, "Command not found")
    found["acked_at"] = datetime.now(timezone.utc).isoformat()
    found["status"] = "acknowledged" if success else "failed"
    found["error"] = None if success else error
    found["result"] = result
    save_db(db)
    return {"success": True, "status": found["status"]}'''
if o2 in c: c = c.replace(o2, r2, 1); n += 1

# 3) queue_command: append to list instead of reject
o3 = '''    db.setdefault("pi_commands", {})
    existing = db["pi_commands"].get(sid)
    if existing and existing.get("status") == "pending":
        return {
            "success": False,
            "message": "A command is already pending. Wait for ACK.",
            "pending_command": existing.get("command"),
            "pending_id": existing.get("id"),
        }

    command_id = str(int(time.time() * 1000))
    db["pi_commands"][sid] = {
        "id": command_id,
        "command": command,
        "wing": wing,
        "params": params,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "acked_at": None,
        "error": None,
        "result": None,
    }
    save_db(db)'''
r3 = '''    command_id = str(int(time.time() * 1000))
    new_cmd = {
        "id": command_id, "command": command, "wing": wing, "params": params,
        "queued_at": datetime.now(timezone.utc).isoformat(), "status": "pending",
        "sent_at": None, "acked_at": None, "error": None, "result": None,
    }
    db.setdefault("pi_commands", {})
    if sid not in db["pi_commands"]: db["pi_commands"][sid] = []
    if isinstance(db["pi_commands"][sid], dict): db["pi_commands"][sid] = [db["pi_commands"][sid]]
    db["pi_commands"][sid].append(new_cmd)
    save_db(db)'''
if o3 in c: c = c.replace(o3, r3, 1); n += 1

# 4) dashboard: show first pending/sent from list
o4 = '''    cmd = db.get("pi_commands", {}).get(int(society_id))
    return {
        "connected": True,
        "active_wing": pi.get("active_wing"),
        "reset_day": pi.get("reset_day", DEFAULT_RESET_DAY),
        "wings": wings_data,
        "emergency_stop": pi.get("emergency_stop", False),
        "watchdog_enabled": pi.get("watchdog_enabled", False),
        "last_reboot_reason": pi.get("last_reboot_reason", ""),
        "firmware_version": pi.get("firmware_version", "?"),
        "cpu_temp": pi.get("cpu_temp", 0),
        "uptime_seconds": pi.get("uptime_seconds", 0),
        "last_sync": pi.get("last_sync"),
        "pending_command": {
            "id": cmd.get("id"), "command": cmd.get("command"),
            "status": cmd.get("status"), "queued_at": cmd.get("queued_at"),
            "acked_at": cmd.get("acked_at"), "error": cmd.get("error"),
        } if cmd else None,
    }'''
r4 = '''    cmds = db.get("pi_commands", {}).get(int(society_id), [])
    if isinstance(cmds, dict): cmds = [cmds]
    pc = next((c for c in cmds if c.get("status") in ("pending","sent")), None)
    return {
        "connected": True,
        "active_wing": pi.get("active_wing"),
        "reset_day": pi.get("reset_day", DEFAULT_RESET_DAY),
        "wings": wings_data,
        "emergency_stop": pi.get("emergency_stop", False),
        "watchdog_enabled": pi.get("watchdog_enabled", False),
        "last_reboot_reason": pi.get("last_reboot_reason", ""),
        "firmware_version": pi.get("firmware_version", "?"),
        "cpu_temp": pi.get("cpu_temp", 0),
        "uptime_seconds": pi.get("uptime_seconds", 0),
        "last_sync": pi.get("last_sync"),
        "pending_command": {
            "id": pc.get("id"), "command": pc.get("command"),
            "status": pc.get("status"), "queued_at": pc.get("queued_at"),
            "sent_at": pc.get("sent_at"), "acked_at": pc.get("acked_at"),
            "error": pc.get("error"),
        } if pc else None,
    }'''
if o4 in c: c = c.replace(o4, r4, 1); n += 1

with open(fp, 'w', encoding='utf-8') as f: f.write(c)
print(f'OK: {n}/4 replacements applied')
