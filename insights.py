"""Monthly helpdesk insights.

Everything here is plain Python arithmetic on ticket dicts (the shape returned by app.ticket_dict).
The numbers are always calculated here, never by the AI. The AI (see app.py) only turns the finished
numbers into plain-language sentences; plain_summary() below does the same without AI.
"""
from datetime import datetime
from statistics import median

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MIN_SAMPLE = 5   # fewer solved tickets than this: averages are only a rough guide
MIN_GROUP = 3    # a category needs this many solved tickets before we call it slow or fast


def fmt_min(m) -> str:
    """12.4 -> '12 min', 75 -> '1 h 15 min'."""
    if m is None:
        return "n/a"
    m = round(m)
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60:02d} min"


def _pct(part: int, whole: int):
    return round(100 * part / whole) if whole else None


def _minutes(t) -> float:
    return (t["resolvedAt"] - t["created"]) / 60000


def _stats(group: list) -> dict:
    """Counts and solve-time numbers for a list of tickets reported in the period."""
    solved = [t for t in group if t["resolvedAt"]]
    mins = sorted(_minutes(t) for t in solved)
    within = sum(1 for t in solved if _minutes(t) < t["slaMinutes"])
    return {"reported": len(group), "solved": len(solved),
            "avgMin": round(sum(mins) / len(mins), 1) if mins else None,
            "withinPct": _pct(within, len(solved))}


def _breakdown(cohort: list, key) -> list:
    groups = {}
    for t in cohort:
        groups.setdefault(key(t), []).append(t)
    rows = [{"name": name, **_stats(ts)} for name, ts in groups.items()]
    return sorted(rows, key=lambda r: (-r["reported"], r["name"]))


def band_name(t) -> str:
    return "Public holiday" if t["slaHoliday"] else t["slaLabel"]


def month_metrics(tickets: list, start: int, end: int, now: int, tz, name_of=lambda x: x) -> dict:
    """Numbers for the tickets REPORTED between start (inclusive) and end (exclusive), in epoch ms."""
    cohort = [t for t in tickets if start <= t["created"] < end]
    solved = [t for t in cohort if t["resolvedAt"]]
    mins = sorted(_minutes(t) for t in solved)
    base = _stats(cohort)
    slow = max(solved, key=_minutes, default=None)
    p90 = mins[min(len(mins) - 1, int(0.9 * len(mins)))] if mins else None
    open_now = [t for t in cohort if not t["resolvedAt"]]
    over = [t for t in open_now if (now - t["created"]) / 60000 >= t["slaMinutes"]]

    local = lambda ms: datetime.fromtimestamp(ms / 1000, tz)
    weekday, hours, daily = [0] * 7, [0] * 24, {}
    for t in cohort:
        d = local(t["created"])
        weekday[d.weekday()] += 1
        hours[d.hour] += 1
        daily[d.day] = daily.get(d.day, 0) + 1
    last_day = (local(end - 1)).day
    by_req = {}
    for t in cohort:
        by_req[t["requester"].strip().lower()] = by_req.get(t["requester"].strip().lower(), 0) + 1

    return {
        **base,
        "open": len(open_now),
        "openOverTarget": len(over),
        "medianMin": round(median(mins), 1) if mins else None,
        "p90Min": round(p90, 1) if p90 is not None else None,
        "fastestMin": round(mins[0], 1) if mins else None,
        "slowestMin": round(mins[-1], 1) if mins else None,
        "slowest": {"id": slow["id"], "subject": slow["subject"], "minutes": round(_minutes(slow))} if slow else None,
        "withinTarget": sum(1 for t in solved if _minutes(t) < t["slaMinutes"]),
        "byType": _breakdown(cohort, lambda t: t["errorType"] or "Unspecified"),
        "byPriority": _breakdown(cohort, lambda t: t["priority"]),
        "byBand": _breakdown(cohort, band_name),
        "byAgent": _breakdown(cohort, lambda t: name_of(t["assignee"])),
        "weekday": weekday,
        "hours": hours,
        "daily": [daily.get(d, 0) for d in range(1, last_day + 1)],
        "repeatRequesters": sum(1 for n in by_req.values() if n >= 3),
        "critical": sum(1 for t in cohort if t["priority"] == "critical"),
    }


def peak(weekday: list, hours: list) -> dict:
    """Busiest weekday and busiest 2-hour window (only when there is something to report)."""
    out = {}
    if sum(weekday):
        i = max(range(7), key=lambda k: weekday[k])
        out["weekday"] = WEEKDAYS[i]
        out["weekdayCount"] = weekday[i]
    if sum(hours):
        h = max(range(24), key=lambda k: hours[k] + hours[(k + 1) % 24])
        out["window"] = f"{h:02d}:00-{(h + 2) % 24:02d}:00"
        out["windowCount"] = hours[h] + hours[(h + 1) % 24]
    return out


def ai_payload(label: str, m: dict, prev: dict, prev_label: str) -> dict:
    """What the AI is allowed to see: totals and groups only. No ticket text, no requester names, no agent names."""
    keep = ("reported", "solved", "open", "openOverTarget", "avgMin", "medianMin", "p90Min", "slowestMin",
            "withinPct", "critical", "repeatRequesters")
    agents = [a for a in m["byAgent"] if a["name"] != "Unassigned"]
    top_share = _pct(max((a["reported"] for a in agents), default=0), m["reported"])
    return {
        "month": label,
        "thisMonth": {k: m[k] for k in keep},
        "previousMonth": {"month": prev_label, **{k: prev[k] for k in ("reported", "solved", "avgMin", "withinPct")}},
        "byErrorType": m["byType"][:8], "byPriority": m["byPriority"], "byTimeBand": m["byBand"],
        "peak": peak(m["weekday"], m["hours"]),
        "workload": {"agentsInvolved": len(agents), "busiestAgentSharePct": top_share,
                     "unassigned": next((a["reported"] for a in m["byAgent"] if a["name"] == "Unassigned"), 0)},
    }


def _delta(now, before):
    """'up 20% (from 30)' style comparison, or None when there is nothing to compare with."""
    if now is None or before is None or before == 0:
        return None
    diff = round(100 * (now - before) / before)
    if diff == 0:
        return "about the same as"
    word = "up" if diff > 0 else "down"
    return f"{word} {abs(diff)}% on"


def plain_summary(label: str, m: dict, prev: dict, prev_label: str) -> dict:
    """Rule-based plain-language summary. Used when there is no AI key, and shown instantly above the AI one."""
    if not m["reported"]:
        return {"headline": f"No tickets were reported in {label}.", "highlights": [], "watch": [], "actions": [],
                "source": "rules"}
    hi, watch, actions = [], [], []
    vol = _delta(m["reported"], prev["reported"])
    hi.append(f"{m['reported']} ticket{'s' if m['reported'] != 1 else ''} reported in {label}"
              + (f", {vol} {prev_label} ({prev['reported']})." if vol else "."))
    if m["solved"]:
        s = f"{m['solved']} solved with an average of {fmt_min(m['avgMin'])} (typical ticket: {fmt_min(m['medianMin'])})."
        if prev["avgMin"] and m["avgMin"] is not None:
            d = m["avgMin"] - prev["avgMin"]
            if abs(d) >= 1:
                s += f" That is {fmt_min(abs(d))} {'slower' if d > 0 else 'faster'} than {prev_label}."
        hi.append(s)
    if m["withinPct"] is not None:
        s = f"{m['withinPct']}% of solved tickets met their target"
        if prev["withinPct"] is not None:
            s += f" (was {prev['withinPct']}% in {prev_label})"
        hi.append(s + ".")
    pk = peak(m["weekday"], m["hours"])
    if pk.get("weekday"):
        hi.append(f"Busiest day: {pk['weekday']} ({pk['weekdayCount']} tickets). "
                  f"Busiest time: {pk['window']} ({pk['windowCount']} tickets).")
    top = m["byType"][0] if m["byType"] else None
    if top and m["reported"] >= MIN_SAMPLE:
        hi.append(f"Most common problem: {top['name']} ({top['reported']} tickets, {_pct(top['reported'], m['reported'])}% of the month).")

    if m["openOverTarget"]:
        watch.append(f"{m['openOverTarget']} unsolved ticket{'s are' if m['openOverTarget'] != 1 else ' is'} already past the target time.")
    if m["open"] and not m["openOverTarget"]:
        watch.append(f"{m['open']} ticket{'s are' if m['open'] != 1 else ' is'} still open.")
    slow_types = [r for r in m["byType"] if r["solved"] >= MIN_GROUP and r["withinPct"] is not None and r["withinPct"] < 60]
    for r in slow_types[:2]:
        watch.append(f"{r['name']} tickets often miss the target: only {r['withinPct']}% met it ({r['solved']} solved, average {fmt_min(r['avgMin'])}).")
    bands = {b["name"]: b for b in m["byBand"] if b["solved"] >= MIN_GROUP and b["withinPct"] is not None}
    o, a = bands.get("Office hours"), bands.get("After office hours")
    if o and a and o["withinPct"] - a["withinPct"] >= 15:
        watch.append(f"After-hours tickets do worse: {a['withinPct']}% met the target, against {o['withinPct']}% in office hours.")
        actions.append("Review who covers after-hours and weekends, and how they are alerted.")
    if m["repeatRequesters"]:
        watch.append(f"{m['repeatRequesters']} requester{'s' if m['repeatRequesters'] != 1 else ''} reported 3 or more tickets, which may point to an unresolved root cause or a training need.")
    unassigned = next((x for x in m["byAgent"] if x["name"] == "Unassigned"), None)
    if unassigned and unassigned["reported"]:
        watch.append(f"{unassigned['reported']} ticket{'s were' if unassigned['reported'] != 1 else ' was'} never assigned to anyone.")
        actions.append("Assign every ticket to an agent when it is logged.")
    if slow_types:
        actions.append(f"Look at recent {slow_types[0]['name']} tickets for a repeat cause or a missing how-to.")
    if m["solved"] and m["solved"] < MIN_SAMPLE:
        watch.append(f"Only {m['solved']} ticket{'s were' if m['solved'] != 1 else ' was'} solved, so treat these averages as a rough guide.")
    if not watch:
        watch.append("Nothing stands out this month.")
    return {"headline": hi[0], "highlights": hi[1:], "watch": watch, "actions": actions, "source": "rules"}


# ------------------------------------------------------------ quality objective
# Cases in the chosen error types must be solved within their target (30 min in normal hours, 45 min after hours
# by default) for at least GOAL percent of cases. Normal hours and after hours are judged separately.
BANDS = (("office", "Normal hours"), ("after", "After hours"))


def _case_state(t, now) -> str:
    """met / late (solved, but not within target) / overdue (unsolved and already past target) / pending."""
    if t["resolvedAt"]:
        return "met" if _minutes(t) < t["slaMinutes"] else "late"
    return "overdue" if (now - t["created"]) / 60000 >= t["slaMinutes"] else "pending"


def _band_numbers(group: list, now: int, goal: float) -> dict:
    states = [_case_state(t, now) for t in group]
    n = {k: states.count(k) for k in ("met", "late", "overdue", "pending")}
    evaluated = n["met"] + n["late"] + n["overdue"]
    rate = round(100 * n["met"] / evaluated, 1) if evaluated else None
    allowed = int(evaluated * (100 - goal) / 100 + 1e-9)   # late cases the goal tolerates at this volume
    return {"cases": len(group), **n, "evaluated": evaluated, "rate": rate,
            "achieved": None if rate is None else rate >= goal,
            "allowedMisses": allowed, "misses": n["late"] + n["overdue"]}


def objective_month(tickets: list, start: int, end: int, now: int, tz, types: list, goal: float) -> dict:
    """Both bands for the tickets reported in [start, end) whose error type is in `types`."""
    lower = {x.lower() for x in types}
    scope = [t for t in tickets if start <= t["created"] < end and t["errorType"].lower() in lower]
    out = {}
    for key, _label in BANDS:
        group = [t for t in scope if t["slaBand"] == key]
        solved_mins = sorted(_minutes(t) for t in group if t["resolvedAt"])
        b = _band_numbers(group, now, goal)
        b.update(
            targetMin=group[0]["slaMinutes"] if group else None,
            avgMin=round(sum(solved_mins) / len(solved_mins), 1) if solved_mins else None,
            medianMin=round(median(solved_mins), 1) if solved_mins else None,
            p90Min=round(solved_mins[min(len(solved_mins) - 1, int(0.9 * len(solved_mins)))], 1) if solved_mins else None,
            slowestMin=round(solved_mins[-1], 1) if solved_mins else None,
            byType=[{"name": name, **_band_numbers(ts, now, goal)} for name, ts in sorted(
                {t["errorType"]: [x for x in group if x["errorType"] == t["errorType"]] for t in group}.items())],
            points=[{"id": t["id"], "day": datetime.fromtimestamp(t["created"] / 1000, tz).day
                     + datetime.fromtimestamp(t["created"] / 1000, tz).hour / 24,
                     "minutes": round(_minutes(t) if t["resolvedAt"] else (now - t["created"]) / 60000, 1),
                     "target": t["slaMinutes"], "state": _case_state(t, now), "subject": t["subject"]}
                    for t in sorted(group, key=lambda x: x["created"])],
            missed=sorted(
                ({"id": t["id"], "subject": t["subject"], "errorType": t["errorType"], "state": _case_state(t, now),
                  "minutes": round(_minutes(t) if t["resolvedAt"] else (now - t["created"]) / 60000), "target": t["slaMinutes"]}
                 for t in group if _case_state(t, now) in ("late", "overdue")), key=lambda r: -r["minutes"]))
        out[key] = b
    return out


def _pts(a, b):
    """Difference in percentage points, as text: 'up 3.2 points' / 'down 1.0 point'."""
    if a is None or b is None:
        return None
    d = round(a - b, 1)
    if d == 0:
        return "unchanged"
    return f"{'up' if d > 0 else 'down'} {abs(d):g} point{'s' if abs(d) != 1 else ''}"


def band_summary(label: str, b: dict, prev: dict, goal: float, types: list, target_text: str) -> list:
    """Plain-language bullets for one band, written by rules (no AI)."""
    names = " and ".join(types) if types else "the chosen error types"
    if not b["cases"]:
        return [f"No {names} cases were reported in {label.lower()} this month."]
    out = []
    if b["rate"] is None:
        out.append(f"{b['cases']} case{'s' if b['cases'] != 1 else ''} reported in {label.lower()}, none due for judging yet "
                   f"(all still within their {target_text} target).")
        return out
    verdict = "meets" if b["achieved"] else "is below"
    out.append(f"{b['rate']:g}% of {b['evaluated']} case{'s' if b['evaluated'] != 1 else ''} met the {target_text} target, "
               f"which {verdict} the {goal:g}% goal.")
    if b["misses"]:
        parts = []
        if b["late"]:
            parts.append(f"{b['late']} solved late")
        if b["overdue"]:
            parts.append(f"{b['overdue']} still open past target")
        out.append(f"{b['misses']} missed ({', '.join(parts)}). At this volume the goal allows {b['allowedMisses']} miss"
                   f"{'es' if b['allowedMisses'] != 1 else ''}.")
    else:
        out.append(f"No misses. The goal would allow {b['allowedMisses']} at this volume.")
    if b["avgMin"] is not None:
        out.append(f"Solved cases took {fmt_min(b['avgMin'])} on average; the slowest took {fmt_min(b['slowestMin'])}.")
    if prev and prev.get("rate") is not None:
        d = _pts(b["rate"], prev["rate"])
        out.append(f"That is {d} on last month ({prev['rate']:g}%)." if d != "unchanged" else f"Same as last month ({prev['rate']:g}%).")
    weak = [r for r in b["byType"] if r["evaluated"] >= MIN_GROUP and r["rate"] is not None and r["rate"] < goal]
    if len(b["byType"]) > 1 and weak:
        w = min(weak, key=lambda r: r["rate"])
        out.append(f"{w['name']} is the weaker type: {w['rate']:g}% of {w['evaluated']} cases met the target.")
    if b["pending"]:
        out.append(f"{b['pending']} case{'s are' if b['pending'] != 1 else ' is'} still open and within target, so not counted yet.")
    if b["evaluated"] < 10:
        out.append(f"Only {b['evaluated']} case{'s' if b['evaluated'] != 1 else ''}, so a single miss moves the rate by "
                   f"{round(100 / b['evaluated'], 1):g} points. Read the trend with care.")
    return out


def objective_ai_payload(cfg: dict, month: dict, trend: list) -> dict:
    """Numbers only (no ticket text) for the AI's objective commentary."""
    keep = ("cases", "met", "late", "overdue", "pending", "evaluated", "rate", "achieved", "allowedMisses",
            "targetMin", "avgMin", "p90Min")
    return {"goalPct": cfg["goalPct"], "errorTypes": cfg["types"],
            "normalHours": {k: month["office"][k] for k in keep},
            "afterHours": {k: month["after"][k] for k in keep},
            "monthlyRates": [{"month": t["label"], "normalHoursPct": t["office"]["rate"], "normalHoursCases": t["office"]["evaluated"],
                              "afterHoursPct": t["after"]["rate"], "afterHoursCases": t["after"]["evaluated"]} for t in trend]}
