"""Best-effort SMART/NAND health collector (hourly, read-only, never blocks control).

SMART is diagnostics only: logical EMS writes / block I/O (storage_io_manager) are NOT NAND wear, and
this module never infers endurance from write counts. It reads only what the device exposes via
`smartctl -a -j` (non-destructive) and reports UNAVAILABLE/ERROR when unsupported."""
import json
import os
import shutil
import subprocess
import time

COLLECT_INTERVAL_S = int(os.environ.get("EMS_SMART_INTERVAL_S", "3600"))     # hourly, never 60 s
SMARTCTL_TIMEOUT_S = 20
# Configurable thresholds; applied ONLY to attributes the device actually reports.
LIFE_WARNING_PCT = int(os.environ.get("EMS_SMART_LIFE_WARNING_PCT", "20"))
LIFE_CRITICAL_PCT = int(os.environ.get("EMS_SMART_LIFE_CRITICAL_PCT", "5"))
TEMP_WARNING_C = int(os.environ.get("EMS_SMART_TEMP_WARNING_C", "70"))
TEMP_CRITICAL_C = int(os.environ.get("EMS_SMART_TEMP_CRITICAL_C", "85"))


def _run_smartctl(device, runner=None):
    """Returns (status, parsed_json_or_None, error). status: AVAILABLE | UNAVAILABLE | ERROR."""
    if shutil.which("smartctl") is None:
        return "UNAVAILABLE", None, "smartctl not installed"
    try:
        run = runner or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, timeout=SMARTCTL_TIMEOUT_S))
        proc = run(["smartctl", "-a", "-j", device])
    except subprocess.TimeoutExpired:
        return "ERROR", None, "smartctl timeout"
    except OSError as exc:
        return "ERROR", None, f"smartctl exec failed: {exc}"
    try:
        data = json.loads(proc.stdout or "")
    except ValueError:
        return "ERROR", None, "malformed smartctl output"
    msgs = data.get("smartctl", {}).get("messages", [])
    if not data.get("device") or any("Unable to detect" in m.get("string", "") or "SMART support is: Unavailable" in m.get("string", "") for m in msgs):
        return "UNAVAILABLE", data, "device does not support SMART"
    if data.get("smart_status") is None and not data.get("nvme_smart_health_information_log") and not data.get("ata_smart_attributes"):
        return "UNAVAILABLE", data, "no SMART attributes reported"
    return "AVAILABLE", data, None


def _attrs(data):
    """Normalise the subset of attributes that may exist; missing -> None (never invented)."""
    out = {"model": (data.get("model_name") or data.get("device", {}).get("name")), "serial": data.get("serial_number"),
           "passed": (data.get("smart_status") or {}).get("passed"), "temperature_c": (data.get("temperature") or {}).get("current"),
           "power_cycles": data.get("power_cycle_count"), "unsafe_shutdowns": None, "data_written_gb": None, "data_read_gb": None,
           "erase_count": None, "remaining_life_pct": None, "program_erase_failures": None, "uncorrectable_errors": None}
    nvme = data.get("nvme_smart_health_information_log") or {}
    if nvme:
        out["unsafe_shutdowns"] = nvme.get("unsafe_shutdowns")
        if nvme.get("data_units_written") is not None: out["data_written_gb"] = round(nvme["data_units_written"] * 512000 / 1e9, 1)
        if nvme.get("data_units_read") is not None: out["data_read_gb"] = round(nvme["data_units_read"] * 512000 / 1e9, 1)
        if nvme.get("percentage_used") is not None: out["remaining_life_pct"] = max(0, 100 - int(nvme["percentage_used"]))
        out["uncorrectable_errors"] = nvme.get("media_errors")
    for a in (data.get("ata_smart_attributes") or {}).get("table", []) or []:
        name, raw, val = a.get("name", ""), (a.get("raw") or {}).get("value"), a.get("value")
        if name in ("Total_LBAs_Written", "Host_Writes_32MiB") and raw is not None:
            out["data_written_gb"] = round(raw * (512 if name == "Total_LBAs_Written" else 33554432) / 1e9, 1)
        elif name in ("Average_Block-Erase_Count", "Erase_Count", "Ave_Block-Erase_Count"): out["erase_count"] = raw
        elif name in ("Percent_Lifetime_Remain", "Remaining_Lifetime_Perc", "SSD_Life_Left"): out["remaining_life_pct"] = val if raw is None or raw > 100 else raw
        elif name in ("Program_Fail_Count_Chip", "Program_Fail_Cnt_Total", "Erase_Fail_Count_Chip", "Erase_Fail_Count_Total"):
            out["program_erase_failures"] = (out["program_erase_failures"] or 0) + (raw or 0)
        elif name in ("Reported_Uncorrect", "Offline_Uncorrectable", "Uncorrectable_Error_Cnt"):
            out["uncorrectable_errors"] = (out["uncorrectable_errors"] or 0) + (raw or 0)
        elif name in ("Unsafe_Shutdown_Count", "Power-Off_Retract_Count"): out["unsafe_shutdowns"] = raw
    return out


def classify(attrs):
    """OK / WARNING / CRITICAL from SUPPORTED indicators only; unknown attributes never raise the level."""
    reasons = []; level = "OK"
    def bump(new, why):
        nonlocal level
        reasons.append(why)
        if new == "CRITICAL" or (new == "WARNING" and level == "OK"): level = new
    if attrs.get("passed") is False: bump("CRITICAL", "SMART overall self-assessment FAILED")
    life = attrs.get("remaining_life_pct")
    if life is not None:
        if life <= LIFE_CRITICAL_PCT: bump("CRITICAL", f"remaining life {life}% <= {LIFE_CRITICAL_PCT}%")
        elif life <= LIFE_WARNING_PCT: bump("WARNING", f"remaining life {life}% <= {LIFE_WARNING_PCT}%")
    if (attrs.get("uncorrectable_errors") or 0) > 0: bump("CRITICAL", f"{attrs['uncorrectable_errors']} uncorrectable errors")
    if (attrs.get("program_erase_failures") or 0) > 0: bump("WARNING", f"{attrs['program_erase_failures']} program/erase failures")
    t = attrs.get("temperature_c")
    if t is not None:
        if t >= TEMP_CRITICAL_C: bump("CRITICAL", f"temperature {t}C >= {TEMP_CRITICAL_C}C")
        elif t >= TEMP_WARNING_C: bump("WARNING", f"temperature {t}C >= {TEMP_WARNING_C}C")
    return level, reasons


def collect(device, runner=None):
    status, data, err = _run_smartctl(device, runner)
    if status != "AVAILABLE":
        return {"smart": status, "health": "UNKNOWN", "device": device, "error": err, "attributes": None, "reasons": []}
    attrs = _attrs(data); level, reasons = classify(attrs)
    return {"smart": "AVAILABLE", "health": level, "device": device, "error": None, "attributes": attrs, "reasons": reasons}


class SmartHealthMonitor:
    """Hourly collector; persists ONLY when the summarised state changes (one tiny file, low frequency)."""
    def __init__(self, device, history_path, logger, interval_s=COLLECT_INTERVAL_S, runner=None):
        self.device, self.path, self.logger, self.interval, self.runner = device, history_path, logger, interval_s, runner
        self._next = 0.0; self.last = None; self.last_result = None

    def maybe_collect(self, now=None):
        now = time.monotonic() if now is None else now
        if now < self._next:
            return None
        self._next = now + self.interval
        try:
            result = collect(self.device, self.runner)
        except Exception as exc:  # diagnostics must never take the controller down
            result = {"smart": "ERROR", "health": "UNKNOWN", "device": self.device, "error": str(exc), "attributes": None, "reasons": []}
        self.last_result = result
        summary = (result["smart"], result["health"], tuple(result["reasons"]))
        if self.last != summary:
            self.last = summary
            self.logger.warning("SMART_HEALTH smart=%s health=%s device=%s reasons=%s error=%s", result["smart"], result["health"], self.device, result["reasons"], result["error"])
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                tmp = self.path + ".tmp"
                with open(tmp, "w") as fh:
                    json.dump({"ts": time.time(), **result}, fh)
                    fh.flush(); os.fsync(fh.fileno())
                os.replace(tmp, self.path)
            except OSError as exc:
                self.logger.error("SMART history not persisted: %s", exc)
        return result
