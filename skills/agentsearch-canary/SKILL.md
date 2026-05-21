---
name: agentsearch-canary
description: Daily health check for the AgentSearch repo — runs `agentsearch canary` on the user's local machine, classifies every adapter as PASS/EMPTY/FAIL, and auto-files a GitHub issue when more than 20% of engines are unhealthy. Use this skill whenever the user asks to "check on agentsearch", "run a canary", "audit which engines are healthy", or as part of a daily/weekly scheduled run. Local execution is mandatory — running this on a CI / cloud IP produces false positives because Reddit / Cloudflare / DataDome rate-throttle datacenter IPs.
version: 1.0.0
metadata:
  short-description: Run agentsearch canary locally; auto-file GitHub issue on regression.
  keywords:
    - canary
    - health check
    - smoke test
    - agentsearch
    - regression detection
    - engine status
    - DOM drift
    - 健康检查
    - 巡检
---

# 🧪 AgentSearch Canary Skill

Run `agentsearch canary` on the user's machine on a schedule (or on
demand) and turn the result into a GitHub issue when regressions
exceed the threshold.

This skill is **machine-local by design**. CI runners use Azure
datacenter IPs that Reddit / Cloudflare / DataDome already pre-block,
so running canary in CI produces false positives. Running it on the
same residential network the user normally browses from gives accurate
signal.

---

## Pre-flight checks

Before running, make sure all four are true on the user's machine.
Each is a one-line shell test the agent should run:

| Check | Command | What "ok" looks like |
|---|---|---|
| Repo present | `test -d ~/projects/AgentSearch && echo ok` | `ok` |
| venv present | `test -x ~/tools/cloakbrowser/venv/bin/agentsearch && echo ok` | `ok` |
| `gh` authed | `gh auth status -h github.com 2>&1 \| head -3` | logged-in line |
| Cache dir | `mkdir -p ~/.cache/agentsearch && echo ok` | `ok` |

If any check fails, surface a precise next step — e.g. `gh auth login`
for the third — and stop. Do **not** run the canary partially.

---

## How to invoke

### One-shot

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd ~/projects/AgentSearch
agentsearch canary --gh-issue \
    --report ~/.cache/agentsearch/canary_report.json
```

The script:

1. Spawns one canary search through every adapter in parallel.
2. Writes a JSON report.
3. If `(EMPTY + FAIL) / total > 20%`, formats a markdown issue body.
4. Calls `gh issue list/create/comment` to post / append the issue
   under the `canary-regression` label (no duplicates).

### Faster subset (quick spot-check)

```bash
agentsearch canary --engines duckduckgo,reddit,arxiv,github_search
```

### Markdown only (no `gh` available)

```bash
agentsearch canary \
    --issue-md ~/.cache/agentsearch/canary_issue.md \
    --report ~/.cache/agentsearch/canary_report.json
```

Then paste `~/.cache/agentsearch/canary_issue.md` into a new GitHub
issue at <https://github.com/t0ken-ai/AgentSearch/issues/new>.

---

## Recommended schedule

Run **once a day**, at a time when the user's network is reliably up
(early afternoon local time is usually safe — many sites have a
maintenance window 02:00-06:00).

If the user has OpenClaw with periodic-task support, add a line like:

> Every day at 09:00 local, invoke this skill. If it exits non-zero
> (regression detected), summarise the issue body in chat too so the
> user sees it without checking GitHub.

If OpenClaw doesn't have a built-in scheduler the user is using, fall
back to one of:

| OS | Mechanism | Template |
|---|---|---|
| macOS | `launchd` | `docs/CANARY.md` § "macOS · launchd" |
| Linux | `systemd --user` timer | `docs/CANARY.md` § "Linux · systemd" |
| Any | `cron` | `docs/CANARY.md` § "Plain cron" |

---

## Reporting back to the user

After every run, surface a one-line summary like:

> 🧪 AgentSearch canary: 78/80 PASS · 2 EMPTY · 0 FAIL — wall-clock 4m 12s.
> Issue #142 filed for `expedia` and `ziprecruiter` (DOM mismatch).

When the run is healthy (no regression), keep the chatter minimal —
just one line confirming the run was clean.

When regressions land, surface the specific engine names and quote the
top error text from the report so the user can decide whether to dig
in immediately or queue it.

---

## Tuning suggestions

If false positives become an issue (one specific engine that's flaky on
the user's IP), exclude it explicitly:

```bash
agentsearch canary --engines $(agentsearch list-engines 2>&1 \
    | awk '/✅/ {print $2}' | grep -v '^expedia$' | paste -sd, -)
```

If the user wants tighter alerting (10% threshold), edit the cron line
to add `--fail-threshold 0.10`.

---

## When NOT to run

- Inside CI / shared runner environments (Azure / AWS / GCP IPs).
- When the user's network is on a VPN that's also a known datacenter
  range — same IP-blocking problem.
- More frequently than every 2 hours — stays kind to upstream sites
  and avoids self-induced rate-limiting.

---

## Why this skill exists

Without it, engine regressions surface only when a user reports a bug —
sometimes weeks after the actual breakage. With daily local canary +
auto-filed issue, the maintainer (or another agent) sees the
regression in `t0ken-ai/AgentSearch/issues` within 24 hours and can
push a selector fix the next day.
