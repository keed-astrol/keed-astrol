#!/usr/bin/env python3
"""
Multi-year contribution snake.

Fetches a user's contribution calendar for several years from the GitHub
GraphQL API and writes an animated SVG in which a snake eats each year's
grid in turn, looping forever.

Usage (in GitHub Actions):
  GITHUB_TOKEN=... python3 snake_years.py --user keed-astrol --out dist
Options:
  --years 2024 2025 2026   years to show (default: current year and the two before)
  --fake                   use random data instead of the API (for local testing)
"""
import argparse
import datetime as dt
import json
import os
import random
import urllib.request

# ---------- layout ----------
PITCH = 14          # distance between cell centres
DOT = 10            # cell size
X0, Y0 = 28, 46     # top-left of the grid
COLS, ROWS = 54, 7
WIDTH = X0 * 2 + COLS * PITCH
HEIGHT = Y0 + ROWS * PITCH + 16

SNAKE_LEN = 5
PRE, POST = 1.0, 1.2          # seconds to show the full / empty grid
MAX_EAT_TIME = 22.0           # cap per-year eating time (s)
BASE_STEP = 0.09              # seconds per cell step when not capped

PALETTES = {
    "dark": {
        "empty": "#161b22",
        "levels": ["#0e4429", "#006d32", "#26a641", "#39d353"],
        "text": "#8b949e",
        "title": "#c9d1d9",
        "snake": "#bd93f9",
        "head": "#ff79c6",
    },
    "light": {
        "empty": "#ebedf0",
        "levels": ["#9be9a8", "#40c463", "#30a14e", "#216e39"],
        "text": "#57606a",
        "title": "#24292f",
        "snake": "#8250df",
        "head": "#bf3989",
    },
}
LEVEL_INDEX = {"FIRST_QUARTILE": 0, "SECOND_QUARTILE": 1, "THIRD_QUARTILE": 2, "FOURTH_QUARTILE": 3}
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date weekday contributionCount contributionLevel } }
      }
    }
  }
}"""


# ---------- data ----------
def fetch_year(user, year, token):
    variables = {
        "login": user,
        "from": f"{year}-01-01T00:00:00Z",
        "to": f"{year}-12-31T23:59:59Z",
    }
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        payload = json.load(r)
    if "errors" in payload:
        raise SystemExit(f"GraphQL error for {year}: {payload['errors']}")
    cal = payload["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    return to_grid(year, cal["weeks"], cal["totalContributions"])


def fake_year(year):
    weeks, days = [], []
    d = dt.date(year, 1, 1)
    while d.year == year:
        wd = (d.weekday() + 1) % 7  # Sunday = 0
        if wd == 0 and days:
            weeks.append({"contributionDays": days})
            days = []
        r = random.random()
        count = 0 if r < 0.7 else random.randint(1, 12)
        level = "NONE" if count == 0 else ["FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE"][min(3, count // 3)]
        days.append({"date": d.isoformat(), "weekday": wd, "contributionCount": count, "contributionLevel": level})
        d += dt.timedelta(days=1)
    weeks.append({"contributionDays": days})
    total = sum(x["contributionCount"] for w in weeks for x in w["contributionDays"])
    return to_grid(year, weeks, total)


def to_grid(year, weeks, total):
    cells = {}          # (col,row) -> level index (-1 = empty)
    month_cols = {}
    for c, w in enumerate(weeks):
        for day in w["contributionDays"]:
            r = day["weekday"]
            cells[(c, r)] = LEVEL_INDEX.get(day["contributionLevel"], -1) if day["contributionCount"] > 0 else -1
            date = dt.date.fromisoformat(day["date"])
            if date.day <= 7 and date.month not in month_cols:
                month_cols[date.month] = c
    return {"year": year, "total": total, "cells": cells, "months": month_cols}


# ---------- snake path ----------
def plan_path(cells):
    """Greedy nearest-target walk on the grid. Returns (path, eat_step)."""
    targets = {p for p, lvl in cells.items() if lvl >= 0}
    pos = (-1, 3)
    path = [pos]
    eat_step = {}

    def step_to(goal):
        nonlocal pos
        horizontal_first = random.random() < 0.5
        while pos != goal:
            c, r = pos
            if horizontal_first and c != goal[0] or (not horizontal_first and r == goal[1]):
                c += 1 if goal[0] > c else -1
            else:
                r += 1 if goal[1] > r else -1
            pos = (c, r)
            path.append(pos)
            if pos in targets:
                targets.discard(pos)
                eat_step[pos] = len(path) - 1

    while targets:
        goal = min(targets, key=lambda t: (abs(t[0] - pos[0]) + abs(t[1] - pos[1]), t))
        step_to(goal)
    # exit off the right edge so the whole body leaves the grid
    step_to((pos[0], 3)) if pos[1] != 3 else None
    step_to((COLS + SNAKE_LEN + 1, 3))
    return path, eat_step


# ---------- SVG ----------
def cx(c):
    return X0 + c * PITCH


def cy(r):
    return Y0 + r * PITCH


def pct(t, total):
    return f"{100 * t / total:.4f}%"


def build_svg(grids, pal):
    random.seed(7)
    plans = []
    t = 0.0
    for g in grids:
        path, eat = plan_path(g["cells"])
        steps = len(path) - 1
        dt_step = min(BASE_STEP, MAX_EAT_TIME / max(steps, 1))
        start = t
        move_start = start + PRE
        end = move_start + steps * dt_step + POST
        plans.append(dict(g=g, path=path, eat=eat, dt=dt_step, start=start, move_start=move_start, end=end))
        t = end
    T = t

    css = [
        f"text{{font-family:-apple-system,'Segoe UI',Helvetica,Arial,sans-serif;fill:{pal['text']};font-size:10px}}",
        f".title{{fill:{pal['title']};font-size:13px;font-weight:600}}",
        ".seg,.yr,.c{animation-timing-function:linear;animation-iteration-count:infinite}",
    ]
    body = []
    for i, p in enumerate(plans):
        g = p["g"]
        s, e = p["start"], p["end"]
        # year visibility
        vis = (f"0%,{pct(s, T)}{{opacity:0}}" if s > 0 else "") + \
              f"{pct(s + 0.0001 if s > 0 else 0, T)},{pct(e - 0.0001, T)}{{opacity:1}}" + \
              (f"{pct(e, T)},100%{{opacity:0}}" if e < T else "")
        css.append(f"@keyframes y{i}{{{vis}}}")
        css.append(f".y{i}{{animation-name:y{i};animation-duration:{T:.3f}s}}")

        parts = [f'<g class="yr y{i}" opacity="{1 if i == 0 else 0}">']
        parts.append(f'<text class="title" x="{X0 - 4}" y="18">{g["year"]}</text>')
        parts.append(f'<text x="{WIDTH - X0 + 4}" y="18" text-anchor="end">{g["total"]:,} contributions</text>')
        for m, c in sorted(g["months"].items()):
            parts.append(f'<text x="{cx(c) - DOT / 2}" y="{Y0 - 10}">{MONTHS[m - 1]}</text>')
        # static empty cells
        for (c, r) in g["cells"]:
            parts.append(f'<rect x="{cx(c) - DOT / 2}" y="{cy(r) - DOT / 2}" width="{DOT}" height="{DOT}" rx="2" fill="{pal["empty"]}"/>')
        # coloured cells that get eaten
        for k, ((c, r), lvl) in enumerate(sorted(g["cells"].items())):
            if lvl < 0:
                continue
            te = p["move_start"] + p["eat"][(c, r)] * p["dt"]
            name = f"e{i}_{k}"
            css.append(f"@keyframes {name}{{0%,{pct(te, T)}{{opacity:1}}{pct(te + 0.001, T)},100%{{opacity:0}}}}")
            parts.append(
                f'<rect class="c" style="animation-name:{name};animation-duration:{T:.3f}s" '
                f'x="{cx(c) - DOT / 2}" y="{cy(r) - DOT / 2}" width="{DOT}" height="{DOT}" rx="2" fill="{pal["levels"][lvl]}"/>'
            )
        # snake keyframes (corners only; linear motion between them)
        path, d = p["path"], p["dt"]
        pts = [0]
        for j in range(1, len(path) - 1):
            a, b, cnext = path[j - 1], path[j], path[j + 1]
            if (b[0] - a[0], b[1] - a[1]) != (cnext[0] - b[0], cnext[1] - b[1]):
                pts.append(j)
        pts.append(len(path) - 1)
        frames = [f"0%{{transform:translate({cx(path[0][0])}px,{cy(path[0][1])}px)}}"]
        for j in pts:
            tj = p["move_start"] + j * d
            frames.append(f"{pct(tj, T)}{{transform:translate({cx(path[j][0])}px,{cy(path[j][1])}px)}}")
        frames.append(f"100%{{transform:translate({cx(path[-1][0])}px,{cy(path[-1][1])}px)}}")
        css.append(f"@keyframes s{i}{{{''.join(frames)}}}")
        for seg in reversed(range(SNAKE_LEN)):
            size = DOT + 2 - seg * 1.4
            delay = seg * d - T  # negative delay = segment trails the head by `seg` steps
            colour = pal["head"] if seg == 0 else pal["snake"]
            parts.append(
                f'<g class="seg" style="animation-name:s{i};animation-duration:{T:.3f}s;animation-delay:{delay:.3f}s">'
                f'<rect x="{-size / 2:.1f}" y="{-size / 2:.1f}" width="{size:.1f}" height="{size:.1f}" rx="{size / 3:.1f}" fill="{colour}"/></g>'
            )
        parts.append("</g>")
        body.append("\n".join(parts))

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">\n'
        f"<style>\n{chr(10).join(css)}\n</style>\n" + "\n".join(body) + "\n</svg>\n"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--out", default="dist")
    ap.add_argument("--years", nargs="*", type=int)
    ap.add_argument("--fake", action="store_true")
    a = ap.parse_args()

    this_year = dt.date.today().year
    years = a.years or [this_year - 2, this_year - 1, this_year]
    token = os.environ.get("GITHUB_TOKEN", "")
    grids = [fake_year(y) if a.fake else fetch_year(a.user, y, token) for y in years]

    os.makedirs(a.out, exist_ok=True)
    for name, pal in (("github-snake-dark.svg", PALETTES["dark"]), ("github-snake.svg", PALETTES["light"])):
        with open(os.path.join(a.out, name), "w") as f:
            f.write(build_svg(grids, pal))
        print("wrote", os.path.join(a.out, name))


if __name__ == "__main__":
    main()
