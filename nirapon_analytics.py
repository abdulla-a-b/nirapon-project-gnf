#!/usr/bin/env python3
"""
nirapon_analytics.py  (implementation template)
===============================================
Readiness analytics for the Nirapon Readiness Core dashboard. Reads the task
register (Apps Script TASKS CSV export, a local tasks.json/csv, or live state
via --url) and writes readiness rollups, a verification gap, and a ranked
Corrective Action Plan (CAP) queue, plus a daily trend snapshot.

Scoring model (mirrors the dashboard exactly)
---------------------------------------------
  Completed   = 100%  (x0.9 if NOT yet Verified — verification gating)
  In Progress =  75%
  Delayed     =  50%
  Not Started =   0%
Area / scope / overall readiness = mean of task scores. Pass threshold = 90%.

Field names match the dashboard task object:
  id, area, scope, task, dept, priority, status, verified,
  startDate, planDate, completionDate, roles{O,R,S,V,A,I}, notes, createdAt
(The TASKS sheet flattens roles into owner/responsible/.../inform columns.)

Usage
-----
  python nirapon_analytics.py --in tasks.csv
  python nirapon_analytics.py --in tasks.json
  python nirapon_analytics.py --url "<APPS_SCRIPT_EXEC_URL>"

Standard library only.
"""

import argparse, csv, json, os, sys, datetime, urllib.request

PASS_THRESHOLD = 90
STATUS_BASE = {"Completed": 100, "In Progress": 75, "Delayed": 50, "Not Started": 0}
HERE = os.path.dirname(os.path.abspath(__file__))


def load_from_url(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        payload = json.loads(r.read().decode("utf-8"))
    return (payload.get("state") or {}).get("tasks", [])


def load_from_json(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("tasks", []) if isinstance(data, dict) else data


def load_from_csv(path):
    tasks = []
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["verified"] = str(row.get("verified", "")).upper() in ("YES", "TRUE", "1")
            tasks.append(row)
    return tasks


def task_score(t):
    base = STATUS_BASE.get(t.get("status", "Not Started"), 0)
    if t.get("status") == "Completed" and not t.get("verified"):
        return round(base * 0.9, 1)
    return base


def mean(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else 0.0


def rollup(tasks, key):
    groups = {}
    for t in tasks:
        groups.setdefault(t.get(key, "—"), []).append(task_score(t))
    return {k: {"pct": mean(v), "count": len(v)} for k, v in sorted(groups.items())}


def analyse(tasks):
    if not tasks:
        return {"error": "no tasks found — set up buildings and log tasks first"}

    overall = mean([task_score(t) for t in tasks])
    by_scope = rollup(tasks, "scope")
    by_area = rollup(tasks, "area")
    by_dept = rollup(tasks, "dept")

    completed = [t for t in tasks if t.get("status") == "Completed"]
    verified = [t for t in completed if t.get("verified")]
    delayed = [t for t in tasks if t.get("status") == "Delayed"]
    not_started = [t for t in tasks if t.get("status") == "Not Started"]
    critical_open = [t for t in tasks
                     if str(t.get("priority", "")).lower() == "critical"
                     and t.get("status") != "Completed"]

    def cap_rank(t):
        pr = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}.get(t.get("priority"), 4)
        st = {"Delayed": 0, "Not Started": 1, "In Progress": 2, "Completed": 3}.get(t.get("status"), 4)
        return (pr, st)

    cap = sorted([t for t in tasks if t.get("status") != "Completed"], key=cap_rank)[:25]
    cap_list = [{
        "id": t.get("id"), "area": t.get("area"), "scope": t.get("scope"),
        "task": t.get("task"), "dept": t.get("dept"),
        "priority": t.get("priority"), "status": t.get("status"),
        "planDate": t.get("planDate", "")
    } for t in cap]

    weakest_scope = min(by_scope.items(), key=lambda kv: kv[1]["pct"])[0] if by_scope else "—"
    strongest_scope = max(by_scope.items(), key=lambda kv: kv[1]["pct"])[0] if by_scope else "—"
    weakest_area = min(by_area.items(), key=lambda kv: kv[1]["pct"])[0] if by_area else "—"

    return {
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "headline": {
            "overall_readiness": overall,
            "pass_threshold": PASS_THRESHOLD,
            "audit_ready": overall >= PASS_THRESHOLD,
            "total_tasks": len(tasks),
            "completed": len(completed),
            "verified": len(verified),
            "verification_gap": len(completed) - len(verified),
            "delayed": len(delayed),
            "not_started": len(not_started),
            "critical_open": len(critical_open),
        },
        "insights": {
            "strongest_scope": strongest_scope,
            "weakest_scope": weakest_scope,
            "weakest_area": weakest_area,
        },
        "by_scope": by_scope,
        "by_area": by_area,
        "by_department": by_dept,
        "cap_priority": cap_list,
    }


def append_history(summary):
    path = os.path.join(HERE, "readiness_history.csv")
    h = summary["headline"]
    row = {
        "date": datetime.date.today().isoformat(),
        "overall": h["overall_readiness"], "completed": h["completed"],
        "verified": h["verified"], "delayed": h["delayed"],
        "critical_open": h["critical_open"], "total": h["total_tasks"],
    }
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not exists:
            w.writeheader()
        w.writerow(row)
    return path


def main():
    ap = argparse.ArgumentParser(description="Nirapon readiness analytics")
    ap.add_argument("--in", dest="infile", help="tasks.json or tasks.csv")
    ap.add_argument("--url", help="Apps Script exec URL (pulls live state)")
    ap.add_argument("--out", default=os.path.join(HERE, "readiness_summary.json"))
    args = ap.parse_args()

    if args.url:
        tasks = load_from_url(args.url)
    elif args.infile and args.infile.endswith(".json"):
        tasks = load_from_json(args.infile)
    elif args.infile and args.infile.endswith(".csv"):
        tasks = load_from_csv(args.infile)
    elif os.path.exists(os.path.join(HERE, "tasks.json")):
        tasks = load_from_json(os.path.join(HERE, "tasks.json"))
    else:
        print("No input. Use --in tasks.csv|tasks.json or --url <exec>", file=sys.stderr)
        sys.exit(1)

    summary = analyse(tasks)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    if "error" in summary:
        print(summary["error"], file=sys.stderr)
        sys.exit(0)  # empty board is a valid state for a fresh implementation

    hist = append_history(summary)
    h = summary["headline"]
    print(f"Overall readiness : {h['overall_readiness']}%  "
          f"({'READY' if h['audit_ready'] else 'NOT READY'} — pass {PASS_THRESHOLD}%)")
    print(f"Tasks             : {h['total_tasks']}  (done {h['completed']}, "
          f"verified {h['verified']}, delayed {h['delayed']}, critical-open {h['critical_open']})")
    print(f"Verification gap  : {h['verification_gap']} completed-but-unverified")
    print(f"Weakest scope     : {summary['insights']['weakest_scope']}")
    print(f"Wrote             : {args.out}")
    print(f"Appended snapshot : {hist}")


if __name__ == "__main__":
    main()
