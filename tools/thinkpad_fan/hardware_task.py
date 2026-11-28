"""Install the bounded interactive hardware warehouse task on its Windows host."""
import datetime as dt
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path("D:/theseus-hardware")
NAME = "Theseus Hardware Warehouse"
SOURCE = Path("E:/local-lab/tools/thinkpad_fan")
NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
ET.register_namespace("", NS)

def task_xml(sid, start):
    def q(name):
        return f"{{{NS}}}{name}"
    task = ET.Element(q("Task"), version="1.4")
    def add(parent, name, value=None, **attrs):
        e = ET.SubElement(parent, q(name), attrs)
        e.text = value
        return e
    triggers = add(task, "Triggers")
    trigger = add(triggers, "CalendarTrigger")
    rep = add(trigger, "Repetition")
    add(rep, "Interval", "PT5M")
    add(rep, "Duration", "P1D")
    add(rep, "StopAtDurationEnd", "false")
    add(trigger, "StartBoundary", start)
    add(trigger, "Enabled", "true")
    add(add(trigger, "ScheduleByDay"), "DaysInterval", "1")
    principal = add(add(task, "Principals"), "Principal", id="Author")
    add(principal, "UserId", sid)
    add(principal, "LogonType", "InteractiveToken")
    add(principal, "RunLevel", "HighestAvailable")
    settings = add(task, "Settings")
    for name, value in (
        ("MultipleInstancesPolicy","IgnoreNew"),
        ("DisallowStartIfOnBatteries","false"),
        ("StopIfGoingOnBatteries","false"),
        ("StartWhenAvailable","true"),
        ("WakeToRun","false"),
        ("ExecutionTimeLimit","PT10M"),
        ("Enabled","true"),
    ):
        add(settings,name,value)
    action = add(add(task,"Actions",Context="Author"),"Exec")
    add(action,"Command",str(ROOT/"venv/Scripts/python.exe"))
    add(action,"Arguments",f'"{SOURCE/"hardware_warehouse.py"}" --seconds 240')
    add(action,"WorkingDirectory",str(ROOT))
    return ET.tostring(task,encoding="utf-16",xml_declaration=True)

def main():
    import csv
    if subprocess.run(["schtasks","/Query","/TN",NAME],capture_output=True).returncode==0:
        raise RuntimeError("task exists; inspect before replacement")
    raw=subprocess.check_output(["whoami","/user","/fo","csv","/nh"])
    sid=next(csv.reader(raw.decode("utf-8",errors="replace").splitlines()))[1]
    if not sid.startswith("S-1-"):
        raise ValueError("invalid SID")
    start=(dt.datetime.now()+dt.timedelta(minutes=1)).replace(microsecond=0).isoformat()
    path=ROOT/"task.xml"
    path.write_bytes(task_xml(sid,start))
    subprocess.run(["schtasks","/Create","/TN",NAME,"/XML",str(path)],check=True)

if __name__=="__main__":
    main()
