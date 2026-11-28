"""Explicitly install a seven-day, interactive local fan diagnostic task."""

import argparse
import csv
import datetime as dt
import subprocess
import time
import xml.etree.ElementTree as ET

from hardware_task import NS, ROOT, SOURCE, gui_subsystem

NAME = "Theseus Fan Acoustic Diagnostics"


def task_xml(sid, start, expires):
    def q(name):
        return f"{{{NS}}}{name}"

    task = ET.Element(q("Task"), version="1.4")

    def add(parent, name, value=None, **attrs):
        e = ET.SubElement(parent, q(name), attrs)
        e.text = value
        return e

    add(
        add(task, "RegistrationInfo"),
        "Description",
        "Local fan acoustics: 15 seconds every five minutes, seven-day diagnostic trial. No WAV or network.",
    )
    trigger = add(add(task, "Triggers"), "TimeTrigger")
    rep = add(trigger, "Repetition")
    add(rep, "Interval", "PT5M")
    add(rep, "Duration", "P7D")
    add(rep, "StopAtDurationEnd", "false")
    add(trigger, "StartBoundary", start)
    add(trigger, "Enabled", "true")
    principal = add(add(task, "Principals"), "Principal", id="Author")
    add(principal, "UserId", sid)
    add(principal, "LogonType", "InteractiveToken")
    add(principal, "RunLevel", "LeastPrivilege")
    settings = add(task, "Settings")
    for name, value in (
        ("MultipleInstancesPolicy", "IgnoreNew"),
        ("DisallowStartIfOnBatteries", "false"),
        ("StopIfGoingOnBatteries", "false"),
        ("StartWhenAvailable", "false"),
        ("WakeToRun", "false"),
        ("ExecutionTimeLimit", "PT1M"),
        ("Enabled", "true"),
    ):
        add(settings, name, value)
    action = add(add(task, "Actions", Context="Author"), "Exec")
    add(action, "Command", str(ROOT / "venv/Scripts/pythonw.exe"))
    add(action, "Arguments", f'"{SOURCE / "acoustic_periodic.py"}" --expires {expires}')
    add(action, "WorkingDirectory", str(ROOT))
    return ET.tostring(task, encoding="utf-16", xml_declaration=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--install", action="store_true", required=True)
    p.parse_args()
    from acoustic_windows import current_endpoint_id

    if not gui_subsystem(ROOT / "venv/Scripts/pythonw.exe"):
        raise RuntimeError("pythonw is not a GUI subsystem executable")
    if subprocess.run(["schtasks", "/Query", "/TN", NAME], capture_output=True).returncode == 0:
        raise RuntimeError("task exists; inspect before replacement")
    sid = next(
        csv.reader(
            subprocess.check_output(["whoami", "/user", "/fo", "csv", "/nh"])
            .decode("utf-8", errors="replace")
            .splitlines()
        )
    )[1]
    if not sid.startswith("S-1-"):
        raise ValueError("invalid SID")
    folder = ROOT / "acoustic-periodic"
    (folder / "records").mkdir(parents=True, exist_ok=True)
    endpoint_id = current_endpoint_id()
    if not endpoint_id:
        raise RuntimeError("no capture endpoint")
    (folder / "endpoint.txt").write_text(endpoint_id, encoding="utf-8")
    start = (dt.datetime.now() + dt.timedelta(minutes=1)).replace(microsecond=0).isoformat()
    expires = int(time.time()) + 7 * 86400
    path = folder / "task.xml"
    path.write_bytes(task_xml(sid, start, expires))
    subprocess.run(["schtasks", "/Create", "/TN", NAME, "/XML", str(path)], check=True)


if __name__ == "__main__":
    main()
