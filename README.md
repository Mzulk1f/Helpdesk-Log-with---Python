# Helpdesk-Log-with---Python
Docker Desktop; Python; VS Code; Claude API; Claude Haiku 5.5
# Deskly – Helpdesk Log

Log errors reported by end users, then record **when each was reported**, **when it was solved**
and **how long it took**. Python (Flask) + SQLite backend, single-page UI.

## Run on Docker Desktop

```bash
docker compose up --build
```

Open http://localhost:8088 and sign in with **admin / admin123**.
You are asked to choose a new password straight away.
Data lives in the `helpdesk-data` volume (`docker compose down -v` wipes it).

## Roles

| | Agent | Superuser |
|---|:-:|:-:|
| Log tickets, assign, add notes, change status / priority / type | ✅ | ✅ |
| Change a **Closed** ticket (revert to Open / In progress / Resolved) | ❌ | ✅ |
| Add / edit / deactivate users, reset passwords | ❌ | ✅ |
| Add / delete error types | ❌ | ✅ |
| Add / remove public holidays (everyone can view the list) | ❌ | ✅ |
| Correct a ticket's reported time | ❌ | ✅ |

New users get a temporary password and must change it at first sign-in.
You cannot demote or deactivate your own account (so there is always a superuser).

## Times

Pick "Reported at" with the date-time picker, or press a quick button
(Now, 15 min ago, 1 h ago, Yesterday ...). The quick buttons call the server, which
calculates the time with Python's `datetime` / `timedelta` / `zoneinfo`.
Everything is shown in `APP_TIMEZONE`. Duration = solved time - reported time.

## CSV export

Click **⬇ CSV** on the Tickets page, then:

- choose whether to select tickets by **Reported date** or **Solved date** (solved tickets only),
- pick **From** and **To** dates (both included; leave one empty for no limit) or press a preset
  (Today, Yesterday, Last 7 days, Last 30 days, This month, Last month, This year),
- the dialog shows how many tickets match, then **Download CSV**.

Presets and the date filtering are calculated by the server in `APP_TIMEZONE`.
The file includes reported time, solved time and minutes to solve, and opens correctly in Excel.

## Solve-time targets and highlighting

Each ticket gets a target based on **when it was reported** (in `APP_TIMEZONE`):

| Reported | Band | Target |
|---|---|---|
| Sun - Thu, 08:00 - 17:00 | Office hours | 30 min |
| Sun - Thu, 17:00 - 08:00 next day | After office hours | 45 min |
| Friday, 08:00 - 13:00 | Office hours | 30 min |
| Friday 13:00 until Sunday 08:00 (all of Saturday) | After office hours | 45 min |
| Any date on the **Holidays** list (whole day) | After office hours | 45 min |

The **Duration** cell turns **yellow** from 5 minutes before the target and **red** once the target is
reached or exceeded. Open tickets are checked live (the table refreshes every 30 seconds);
solved tickets are judged on their final duration. Hover over a duration to see its target.
The CSV adds `time_band`, `target_minutes` and `within_target` columns.

The rule is applied to the ticket's reported time only. A ticket reported at 16:50 keeps the 30-minute office-hours
target even if it runs past 17:00, and one reported at 07:50 keeps the 45-minute target after 08:00. The numbers can be changed with `SLA_OFFICE_MINUTES`, `SLA_AFTER_MINUTES`
and `SLA_WARN_MINUTES` in `docker-compose.yml`; the hours are constants near the top of `app.py`.

## Holidays

A superuser opens **Holidays**, enters a name and a date (or a From-To range for several days, up to 31)
and presses Add. Everything reported on those dates is after office hours for the whole day, so the 45-minute
target applies. Agents can see the list but not change it.

Adding or removing a holiday recalculates the target of every ticket reported on that date, including old ones,
so past reports (and the CSV `within_target` column) always follow the current list.

## AI features

| Feature | Where | Needs a key? |
|---|---|---|
| **Suggest error type and priority** | New-ticket form: appears about a second after you stop typing the subject/description, or press **✨ Suggest**. Press **Apply** to accept; nothing changes by itself. | Uses AI when a key is set; otherwise suggests from similar past tickets |
| **Draft a cause-and-fix summary** | Ticket panel > Resolution > **✨ Draft with AI**. Write what you found and did in the Resolution box (rough wording is fine) and/or in ticket notes, then press Draft: it tidies this into a short **Cause / Fix**. Edit it, then **Save**. It only uses facts you wrote and says "Not stated in the notes" instead of guessing. | Yes |
| **Similar solved tickets** | Ticket panel and the new-ticket form: past solved tickets with a similar problem, how long they took and what fixed them. | No (runs locally) |

Saved resolutions are included in the CSV export and make future similar-ticket matches better,
so the more tickets you resolve with notes, the more useful the suggestions become.

**Setup:** create a file named `.env` next to `docker-compose.yml` with `ANTHROPIC_API_KEY=sk-ant-...`
(see `.env.example`), then `docker compose up -d --build`. The default model is `claude-haiku-5-5`
(fast and low cost); change it with `AI_MODEL`.

**Privacy and cost:** when AI is used, the subject, description and notes of that ticket (and the subject/type/priority
of up to 5 similar past tickets) are sent to Anthropic's API; the PC running Docker needs internet access.
Similar-ticket search never leaves your machine. Each user is limited to 40 AI requests per 10 minutes (Insights counts too).
AI output is only ever a suggestion that a person confirms.

## Monthly insights

Open **Insights** in the sidebar and pick a month (everyone can see it). It shows tickets reported, solved,
average time to solve and the share that met their target, each compared with the previous month, plus
tickets per day, busiest weekdays, and breakdowns by error type, time of report (office / after hours / holiday),
priority and agent. A month is made of the tickets **reported** in it.

- **Quick summary** is written instantly by plain rules, with no AI and no cost.
- **Explain with AI** (needs a key) turns the same numbers into a short plain-language report: what happened,
  what to watch and suggested next steps. It only runs when you press the button, and the result is kept
  until the app restarts, so pressing it again for unchanged numbers costs nothing. **Regenerate** asks again.
- All numbers are calculated in Python (`insights.py`); the AI never calculates. It is told not to invent causes,
  to be careful with small samples, and never to name or rank people.
- The AI receives totals only: no ticket subjects, descriptions, notes, requester names or agent names.
- The agent table shows workload only (reported / solved), not speed, so it is not used to rank people.

## Quality objective (Insights)

At the top of **Insights** two separate panels, **Normal hours** and **After hours**, report the department objective
for the configured error types (default **Vesalius** and **ICare**): solve within **30 min** in normal hours and
**45 min** after hours, goal **95%**. Each panel has its own rate, allowed misses, a dot chart of every case's solve
time against the target line, a 6-month achievement chart, a by-type table, the missed cases (click to open) and a
plain-language reading (rules always; AI wording when you press *Explain with AI*).

- A case is **missed** if solved late or still open past the target; still open within target is *pending* and not counted.
  Rate = met / (met + late + overdue).
- A **superuser** changes the target minutes, goal % and error types with **Change objective**. Values are stored in the
  database; `OBJECTIVE_PERCENT` / `OBJECTIVE_ERROR_TYPES` / `SLA_*` in docker-compose only give the starting values.
- Changing target minutes recalculates **all** tickets, past months included (no history of old targets is kept).
- Normal hours / after hours follow the same rule as ticket colours (holidays count as after hours).
- The error types must exist under Error types (fresh databases include Vesalius and ICare; on an existing database a
  superuser adds them). A warning shows if one is missing.
- CSV export has an extra column `in_quality_objective`.

## Settings (docker-compose.yml)

`APP_TIMEZONE`, `SEED_DEMO`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ANTHROPIC_API_KEY`, `AI_MODEL`, optional `SECRET_KEY`.
With `SEED_DEMO: "1"` on a fresh database you also get 8 sample tickets and demo agents
(`aisha`, `daniel`, `mei`, `raj`, password `agent1234`) - for demos only.

## Upgrading from the earlier version

The database is upgraded automatically. If it was a demo database, the simplest route is
`docker compose down -v` and start fresh.

## Backup

```bash
docker compose cp helpdesk:/data/helpdesk.db ./backup.db
```

## Not included (yet)

HTTPS, and login lockout beyond a simple in-memory throttle: run it on a trusted network.
