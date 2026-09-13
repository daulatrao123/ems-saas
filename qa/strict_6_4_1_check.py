"""Standalone offline release gate for EMS 6.4.1.

This is a source/invariant gate only. It does not certify real GPIO,
contactor feedback, power-fail behavior, flash endurance, OTA security,
or production deployment.
"""
from pathlib import Path
import hashlib

ROOT = Path(__file__).resolve().parents[1]
PROTECTED = {
    "pi_firmware/gpio_manager.py": "b396b0d074b20f7fc7363bbbca0329c0471d9a54afbd598351f70431893c2dc8",
    "pi_firmware/storage_io_manager.py": "f41617296de4fda888fedb952f953d7db066ae57a9ee3bd75db9119782644fc5",
    # Phase 0.5 re-baseline (June 2026): storage bands 70/80/90/95 + STORAGE_FAILED,
    # monitor_once(), transition events, free_mb/storage_state status, cleanup at >=80%.
    "pi_firmware/storage_manager.py": "73319f7ddca2fdf4aa10df52279f134566873ba047df4c57a2b5f4339abe9b91",
    "pi_firmware/logger.py": "8e1ccc99f4944d87749ee99cc4e09bf41b8be454fa0e2b3d0ff037534e2bdec6",
    "pi_firmware/memory_manager.py": "c353672f37ac706cb3f961a87d19c48895892fafefead5e933183d29719ce5eb",
    # Phase 0.5 re-baseline (June 2026): NORMAL/WARNING/CLEANUP_ELIGIBLE/CRITICAL/
    # STORAGE_PROTECTION/STORAGE_FAILED classification; write policy per band;
    # critical_log/state/queue_db still always allowed.
    "pi_firmware/resource_guard.py": "bd5efcdd44a5a12e5afaa256505402217d2101fa1bb68bfaad763abf08920352",
}

def text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")

def check(name, condition):
    if not condition:
        raise SystemExit(f"FAIL: {name}")
    print(f"PASS: {name}")

backend=text("backend/main.py")
state=text("pi_firmware/state.py")
queue=text("pi_firmware/offline_queue.py")
ctrl=text("pi_firmware/ems_controller.py")
api=text("pi_firmware/api_client.py")
setup=text("pi_firmware/setup_pi.sh")
unit=text("pi_firmware/ems-controller.service")
login=text("frontend/src/app/login/page.tsx")
admin=text("frontend/src/app/admin/page.tsx")
member=text("frontend/src/app/member/page.tsx")
superadmin=text("frontend/src/app/super-admin/page.tsx")
tests=text("frontend/test_system.py")

check("state format remains version 4", "STATE_VERSION = 4" in text("pi_firmware/config.py"))
check("legacy usage fields default safely", 'data.get("used_days", 0)' in state and 'data.get("clicks", 0)' in state)
check("legacy reset fields default safely", 'data.get("last_reset_period")' in state)
claim=queue[queue.index("def claim_next"):queue.index("def get_interrupted")]
check("UNKNOWN_AFTER_REBOOT is excluded from normal claim", "UNKNOWN_AFTER_REBOOT" not in claim and "status='DELIVERED'" in claim)
check("reboot recovery tracks all required statuses", all(x in queue for x in ("EXECUTING", "HARDWARE_VERIFIED", "UNKNOWN_AFTER_REBOOT")))
check("backend queued transition is strict", '"queued": {"delivered", "expired"}' in backend)
check("120-second delivery lease", "COMMAND_DELIVERY_LEASE_SECONDS = 120" in backend)
check("300-second absolute expiry", "COMMAND_EXPIRY_SECONDS = 300" in backend)
check("positive HARDWARE_VERIFIED verification required", "HARDWARE_VERIFIED requires positive hardware verification" in backend)
check("Pi credentials use headers", '"X-Device-ID"' in api and '"X-API-Key"' in api)
check("Pi API key is not duplicated into JSON", 'payload["key"]' not in api)
check("device retirement is non-destructive", "DELETE FROM pi_devices" not in backend and "status='RETIRED'" in backend)
check("retired devices cannot be reassigned", "Retired device cannot be reassigned" in backend)
check("firmware download validates device credentials", "authenticate_pi({}, x_device_id, x_api_key)" in backend)
check("feedback capability is enforced in dashboard", 'bool(c["feedback_enabled"]) and bool(dev["feedback_hardware_installed"])' in backend)
check("storage device is explicit", "EMS_DATA_DEVICE" in setup)
check("setup never formats the data device", "NO FORMAT OPERATION WILL BE PERFORMED" in setup)
check("data mount permissions are root:pi 0770", "chown root:pi" in setup and "chmod 0770" in setup)
check("systemd service runs as pi", "User=pi" in unit and "Group=pi" in unit)
check("systemd requires EMS data mount", "RequiresMountsFor=/mnt/ems-data" in unit)
# ---- member read-only invariant: structural evidence, not UI wording -------------------------------------
# Member page -> OperationalDashboard(readOnly) -> useOperations (the only write surface). We prove:
# (a) member-only guard, (b) readOnly=true is passed, (c) member page touches no write API/control,
# (d) the dashboard's write paths are enumerated and every one is gated by readOnly (one level deeper).
import re
OPS = "frontend/src/components/ops/"
dash = text(OPS + "OperationalDashboard.tsx")
hook = text(OPS + "useOperations.ts")

def strip_readonly_gated(src):
    """Remove every brace-matched `{!readOnly && ...}` JSX block: what remains is what a member can reach."""
    while True:
        i = src.find("{!readOnly &&")
        if i < 0:
            return src
        depth = 0
        for j in range(i, len(src)):
            depth += (src[j] == "{") - (src[j] == "}")
            if depth == 0:
                break
        else:
            raise SystemExit("FAIL: unbalanced {!readOnly && ...} block in OperationalDashboard/child")
        src = src[:i] + src[j + 1:]

od_tag = member[member.index("<OperationalDashboard"):]; od_tag = od_tag[:od_tag.index(">") + 1]
check("member UI is read-only: page is role-guarded to member only", 'useRoleSession(["member"])' in member)
check("member UI is read-only: OperationalDashboard rendered with readOnly=true", re.search(r"\sreadOnly(\s|/|>|=\{true\})", od_tag) is not None and "readOnly={false}" not in od_tag)
check("member UI is read-only: page references no command API or write-capable control", not any(t in member for t in ("/api/admin/pi-command", "pi-command", "slot-config", "SystemControls", "ResetDayControl", "UnitAllotment", "LcdControl", "useOperations", "api.post", "api.put", "api.delete")))
check("member UI is read-only: readOnly is a mandatory boolean prop of OperationalDashboard", "readOnly: boolean" in dash and "readOnly?:" not in dash)
writes = sorted(u for _, u in re.findall(r"api\.(post|put|delete|patch)\(\s*[\"'`]([^\"'`]+)", hook))
check("member UI is read-only: useOperations write surface is exactly pi-command (queue) + slot-config (setSlotConfig)", writes == ["/api/admin/pi-command", "/api/admin/slot-config"] and "const queue: QueueFn" in hook and "const setSlotConfig: SlotConfigFn" in hook)
reach = strip_readonly_gated(dash)
panels = ("SystemControls", "ResetDayControl", "UnitAllotment", "LcdControl")
check("member UI is read-only: control panels render only behind !readOnly", all(f"<{c}" in dash for c in panels) and not any(f"<{c}" in reach for c in panels))
carrier_tags = {}
for m in re.finditer(r"ops\.(queue|setSlotConfig)\b", reach):
    tag = reach[reach.rfind("<", 0, m.start()):]; tag = tag[:tag.index(">") + 1]
    carrier_tags[re.match(r"<(\w+)", tag).group(1)] = tag
for comp, tag in sorted(carrier_tags.items()):
    check(f"member UI is read-only: {comp} receives write fn together with readOnly={{readOnly}}", "readOnly={readOnly}" in tag)
carriers = set(carrier_tags)
check("member UI is read-only: write functions reach at least one child (check is not vacuous)", len(carriers) >= 1)
for comp in sorted(carriers):
    csrc = text(OPS + comp + ".tsx")
    check(f"member UI is read-only: {comp} invokes queue()/setSlotConfig() only behind !readOnly", re.search(r"\b(queue|setSlotConfig)\(", csrc) is not None and re.search(r"\b(queue|setSlotConfig)\(", strip_readonly_gated(csrc)) is None)
mutating_children = []
for comp in sorted(set(re.findall(r"<([A-Z]\w+)", reach))):
    p = ROOT / (OPS + comp + ".tsx")
    if p.exists() and re.search(r"api\.(post|put|delete|patch)\(", p.read_text(encoding="utf-8")):
        csrc = p.read_text(encoding="utf-8"); mutating_children.append(comp)
        check(f"member UI is read-only: {comp} (member-reachable, calls a mutating API) returns null on readOnly before any render", "if (readOnly) return null;" in csrc and csrc.index("if (readOnly) return null;") < csrc.index("return ("))
check("member UI is read-only: mutating member-reachable children enumerated (LcdMessagePanel)", mutating_children == ["LcdMessagePanel"])
check("login routes roles separately", 'router.push("/member")' in login and 'router.push("/admin")' in login)
# ---- role gates (#1 admin, #2 super-admin): structural evidence from OpsShell.useRoleSession, not role strings ----
opsshell = text(OPS + "OpsShell.tsx")
authlib = text("frontend/src/lib/auth.ts")
def role_hook_calls(src): return re.findall(r"useRoleSession\(\[([^\]]*)\]\)", src)
def roles_of(call): return sorted(re.findall(r'"(\w+)"', call))
hook_body = opsshell[opsshell.index("export function useRoleSession(allowed: Role[])"):opsshell.index("return { session, ready };")]
check("role gate: Role type is closed to super_admin/society_admin/member", 'type Role = "super_admin" | "society_admin" | "member";' in opsshell)
check("role gate: useRoleSession resolves the session server-side (/api/auth/me) and treats any failure as no session", "/api/auth/me" in authlib and "return null;" in authlib[authlib.index("export async function getSession"):])
check("role gate: useRoleSession redirects to /login when there is no session or the role is not in the allowed list", 'if (!s || !allowed.includes(s.role as Role)) { router.push("/login"); return; }' in hook_body)
check("role gate: ready starts false and session/ready are set only after the allowed-role check", "useState(false)" in hook_body and hook_body.index("router.push(\"/login\"); return;") < hook_body.index("setSession(s); setReady(true);"))
def page_guarded(src, roles):
    """page calls useRoleSession exactly once with exactly `roles`, and renders nothing but a placeholder until ready && session."""
    calls = role_hook_calls(src)
    guard = src.find("if (!ready || !session) return")
    return len(calls) == 1 and roles_of(calls[0]) == sorted(roles) and guard > 0 and guard < src.index("<OpsShell")
check("admin page enforces society_admin: useRoleSession([\"society_admin\"]) once; operational UI rendered only after ready && session", page_guarded(admin, ["society_admin"]))
check("admin page enforces society_admin: renders the write-capable dashboard (readOnly={false}), never member mode", "readOnly={false}" in admin and re.search(r"<OperationalDashboard[^>]*\sreadOnly(\s|/|>|=\{true\})", admin) is None)
check("admin page enforces society_admin: tenant is taken from the session (society_id), not from the URL or user input", "session.society_id" in admin and "useSearchParams" not in admin and "useParams" not in admin)
pages = sorted((ROOT / "frontend/src/app").rglob("page.tsx"))
dash_pages = [p for p in pages if "<OperationalDashboard" in p.read_text(encoding="utf-8")]
for p in dash_pages:
    src = p.read_text(encoding="utf-8"); calls = role_hook_calls(src); roles = roles_of(calls[0]) if len(calls) == 1 else None
    od = src[src.index("<OperationalDashboard"):]; od = od[:od.index(">") + 1]
    if "readOnly={false}" in od:
        ok = roles is not None and "member" not in roles                      # write-capable only for non-member gates
    elif 'readOnly={session.role === "member"}' in od:
        ok = roles is not None                                                 # member sessions are forced read-only
    else:
        ok = roles is not None and re.search(r"\sreadOnly(\s|/|>|=\{true\})", od) is not None   # literal read-only
    check(f"write-capable dashboard only behind an authenticated non-member role gate: {p.relative_to(ROOT)}", ok)
check("write-capable dashboard pages enumerated (admin, member, society/[id])", [str(p.relative_to(ROOT)) for p in dash_pages] == ["frontend/src/app/admin/page.tsx", "frontend/src/app/member/page.tsx", "frontend/src/app/society/[id]/page.tsx"])
check("super-admin page enforces super_admin: useRoleSession([\"super_admin\"]) once; fleet UI rendered only after ready && session", page_guarded(superadmin, ["super_admin"]))
check("super-admin page enforces super_admin: fleet data is fetched only once the role gate passed", "if (ready) load()" in superadmin and "/api/super-admin/" in superadmin)
check("super-admin page enforces super_admin: tenant/user provisioning forms live only on this surface", "CreateSocietyForm" in superadmin and "CreateUserForm" in superadmin and not any("CreateSocietyForm" in p.read_text(encoding="utf-8") for p in pages if p.name == "page.tsx" and "super-admin" not in str(p)))
sa_routes = re.findall(r'@app\.(?:get|post|put|delete|patch)\("/api/super-admin[^"]*"\)\n(?:@[^\n]*\n)*(?:async )?def \w+\((.*?)\)(?:\s*->[^:]*)?:\n', backend, re.S)
check("super-admin API surface is backend-enforced: every /api/super-admin route depends on require_role(\"super_admin\")", len(sa_routes) == backend.count('"/api/super-admin') and len(sa_routes) >= 1 and all('require_role("super_admin")' in sig for sig in sa_routes) and 'if user.get("role") not in roles:' in backend)
check("command API rejects member role", 'Only super_admin or society_admin may issue commands' in backend)
check("integration test uses environment credentials", "TEST_USERS" in tests and "admin123" not in tests)
# ---- #3 storage_health: the module is the active, non-destructive data-storage health diagnostic ----------------
sh_path = ROOT / "pi_firmware/storage_health.py"
check("storage_health module present and wired into the controller", sh_path.exists() and "from storage_health import (" in ctrl)
sh = sh_path.read_text(encoding="utf-8")
sh_imports = re.findall(r"^(?:import|from)\s+(\w+)", sh, re.M)
check("storage_health uses only os/time: no subprocess/shutil, so it cannot invoke mkfs/fsck/dd or repair tools", sorted(set(sh_imports)) == ["os", "time"])
check("storage_health never formats/erases/repairs (no mkfs/fsck/wipefs/dd/rmtree/rmdir/truncate/unlink tokens)", not re.search(r"mkfs|fsck|wipefs|\bdd\b|rmtree|rmdir|truncate|unlink|blkdiscard|parted|fdisk", sh))
check("storage_health's only file mutation is its own probe file: written, fsynced, read back, removed", 'WRITE_PROBE_NAME = ".ems_write_probe"' in sh and "path = os.path.join(data_dir, WRITE_PROBE_NAME)" in sh and sh.count("os.remove(") == sh.count("os.remove(path)") == 2 and sh.count('open(path, "wb")') == 1 and "os.fsync(fh.fileno())" in sh and sh.count('open(') == sh.count('open(path, "wb")') + sh.count('open(path, "rb")') + sh.count('open("/etc/fstab")') + sh.count('open("/proc/mounts")') + sh.count('open(p).read()'))
check("storage_health health functionality present (collect_storage_health/write_probe/classify/classify_secondary/summarize_smart/device_type)", all(f"def {f}(" in sh for f in ("collect_storage_health", "write_probe", "classify", "classify_secondary", "summarize_smart", "device_type", "expected_uuid", "mounted_uuid")))
check("storage_health secondary-volume verdicts are exactly HEALTHY/UNAVAILABLE/UNWRITABLE", "SECONDARY_STATES = (SECONDARY_HEALTHY, SECONDARY_UNAVAILABLE, SECONDARY_UNWRITABLE)" in sh)
check("storage_health usage bands 90% WARNING / 98% FAILED", "USAGE_WARNING_PERCENT = 90.0" in sh and "USAGE_FAILED_PERCENT = 98.0" in sh)
check("storage_health never reports GOOD without positive SMART evidence (UNAVAILABLE/WARNING -> WARNING; missing SMART -> UNAVAILABLE)", 'if smart_state in ("UNAVAILABLE", "WARNING")' in sh and 'return "UNAVAILABLE", False' in sh)
for rel, expected in PROTECTED.items():
    actual=hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()
    check(f"protected subsystem unchanged: {rel}", actual == expected)
print("\nSTRICT 6.4.1 SOURCE GATE: PASS")
print("NOTE: HIL, endurance, OTA, migrations, dependency and production-auth qualification remain mandatory.")
