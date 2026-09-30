#!/usr/bin/env python3
"""Double Dipper weekly recap: Sleeper stats -> facts -> Claude-written jokes -> static site in docs/.

  python recap.py              recap the latest scored week, then rebuild docs/
  WEEK=3 python recap.py       recap a specific week
  FRESH=true python recap.py   rewrite this week's jokes even if they already exist
  python recap.py render       rebuild docs/ from weeks/*.json only (no network)
"""
import html
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.request import Request, urlopen

LEAGUE = os.environ.get("LEAGUE_ID", "1393873211271188480")
SITE = os.environ.get("SITE_URL", "https://double-dip.alexeldeib.xyz/")
MODEL = "claude-opus-5-5"
ROOT = Path(__file__).resolve().parent
SLEEPER = "https://api.sleeper.app/v1"
PROJ = ("https://api.sleeper.com/projections/nfl/{}/{}?season_type=regular"
        "&position%5B%5D=QB&position%5B%5D=RB&position%5B%5D=WR&position%5B%5D=TE&position%5B%5D=K&position%5B%5D=DEF")
FLEX = {"FLEX": {"RB", "WR", "TE"}, "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
        "REC_FLEX": {"WR", "TE"}, "WRRB_FLEX": {"WR", "RB"}}
HURT = {"Out", "IR", "Doubtful", "Sus", "PUP", "NA"}
STATS = [("pass_yd", "pass yds"), ("pass_td", "pass TD"), ("pass_int", "INT"), ("rush_yd", "rush yds"),
         ("rush_td", "rush TD"), ("rec", "rec"), ("rec_yd", "rec yds"), ("rec_td", "rec TD"),
         ("fum_lost", "fumbles lost")]


def get(url, fallback=None):
    """Fetch JSON. Required endpoints raise; optional ones pass a fallback."""
    try:  # api.sleeper.com 403s the default Python user agent
        with urlopen(Request(url, headers={"User-Agent": "double-dipper-recap"}), timeout=90) as r:
            return json.load(r)
    except Exception:
        if fallback is None:
            raise
        return fallback


def pairs(matchups):
    games = defaultdict(list)
    for m in matchups:
        if m.get("matchup_id"):
            games[m["matchup_id"]].append(m)
    return [g for _, g in sorted(games.items()) if len(g) == 2]


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def build_facts(week=None):
    api = f"{SLEEPER}/league/{LEAGUE}"
    lg, users, rosters = get(api), get(api + "/users"), get(api + "/rosters")
    P = get(f"{SLEEPER}/players/nfl")
    season, cfg = lg["season"], lg["settings"]
    week = int(week or cfg.get("last_scored_leg") or 0)
    if week < 1:
        sys.exit("No scored weeks yet.")
    slots = [s for s in lg["roster_positions"] if s not in ("BN", "IR", "TAXI")]
    pts_key = {1: "pts_ppr", 0.5: "pts_half_ppr"}.get(lg["scoring_settings"].get("rec", 0), "pts_std")

    user = {u["user_id"]: u for u in users}
    teams, team = [], {}
    for r in sorted(rosters, key=lambda r: r["roster_id"]):
        u = user.get(r["owner_id"]) or {}
        manager = u.get("display_name") or f"team{r['roster_id']}"
        team[r["roster_id"]] = (u.get("metadata") or {}).get("team_name") or manager
        avatar = (u.get("metadata") or {}).get("avatar") or (
            u.get("avatar") and f"https://sleepercdn.com/avatars/thumbs/{u['avatar']}")
        teams.append(dict(team=team[r["roster_id"]], manager=manager, avatar=avatar, commish=bool(u.get("is_owner"))))

    def info(pid):
        return P.get(pid) or {}

    def fits(pid, slot):
        p = info(pid)
        return bool(set(p.get("fantasy_positions") or [p.get("position") or "DEF"]) & FLEX.get(slot, {slot}))

    def name(pid):
        p = info(pid)
        if pid == "0":
            return "an empty slot"
        if p.get("position") == "DEF" or not p.get("last_name"):
            return f"{pid} D/ST"
        return f"{p['first_name'][:1]}. {p['last_name']}"

    def optimal(pp):
        # ponytail: fill the pickiest slots first. Exact when slots nest (QB < FLEX < SUPER_FLEX);
        # overlapping flexes (REC_FLEX vs WRRB_FLEX) can come up a hair short.
        left, total = dict(pp), 0.0
        for slot in sorted(slots, key=lambda s: len(FLEX.get(s, {s}))):
            best = max((p for p in left if fits(p, slot)), key=left.get, default=None)
            if best is not None:
                total += left.pop(best)
        return round(total, 2)

    # Season to date: record, all-play, bench points left.
    weekly = {w: get(f"{api}/matchups/{w}") for w in range(1, week + 1)}
    last_regular = min(week, cfg.get("playoff_week_start", 99) - 1)
    rec = {rid: dict(w=0, l=0, t=0, hw=0, h2h=0, pf=0.0, pa=0.0, ap_w=0, ap_l=0, bench=0.0) for rid in team}
    for w in range(1, last_regular + 1):
        ms = [m for m in weekly[w] if m.get("matchup_id")]
        score = {m["roster_id"]: m["points"] for m in ms}
        for m in ms:
            r, others = rec[m["roster_id"]], [v for k, v in score.items() if k != m["roster_id"]]
            r["pf"] += m["points"]
            r["bench"] += optimal(m["players_points"]) - m["points"]
            r["ap_w"] += sum(m["points"] > v for v in others)
            r["ap_l"] += sum(m["points"] < v for v in others)
        for a, b in pairs(ms):
            for x, y in ((a, b), (b, a)):
                r = rec[x["roster_id"]]
                r["pa"] += y["points"]
                r["h2h"] += 1
                r["hw"] += x["points"] > y["points"]
                r["w" if x["points"] > y["points"] else "l" if x["points"] < y["points"] else "t"] += 1
        if cfg.get("league_average_match"):
            v = sorted(score.values())
            median = (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2
            for rid, p in score.items():
                rec[rid]["w" if p > median else "l"] += 1
    standings = []
    for rid, r in rec.items():
        ap = r["ap_w"] + r["ap_l"]
        standings.append(dict(team=team[rid], w=r["w"], l=r["l"], t=r["t"], pf=round(r["pf"], 2), pa=round(r["pa"], 2),
                              all_play=f"{r['ap_w']}-{r['ap_l']}", bench_left=round(r["bench"], 1),
                              pa_per_game=round(r["pa"] / r["h2h"], 1) if r["h2h"] else 0,
                              luck=round(r["hw"] - (r["ap_w"] / ap * r["h2h"] if ap else 0), 2)))
    standings.sort(key=lambda s: (s["w"] + s["t"] / 2, s["pf"]), reverse=True)

    # This week, per team.
    stats = get(f"{SLEEPER}/stats/nfl/regular/{season}/{week}", {})
    proj = {x["player_id"]: (x.get("stats") or {}).get(pts_key) or 0 for x in get(PROJ.format(season, week), [])}

    def line(pid):
        s = stats.get(pid) or {}
        return ", ".join(f"{s[k]:g} {label}" for k, label in STATS if s.get(k))

    ms = [m for m in weekly[week] if m.get("matchup_id")]
    card, lineups = {}, {}
    for m in ms:
        rid, pp, st = m["roster_id"], m["players_points"], m["starters"]
        bench = [p for p in m["players"] if p not in st]
        opt = optimal(pp)
        gain, b, s = max(((pp.get(b, 0) - pp.get(s, 0), b, s)
                          for s, slot in zip(st, slots) for b in bench if fits(b, slot)), default=(0, None, None))
        top = max((p for p in st if p != "0"), key=lambda p: pp.get(p, 0))
        card[rid] = dict(
            team=team[rid], pts=m["points"], opt=opt, eff=round(100 * m["points"] / opt, 1) if opt else 100.0,
            proj=round(sum(proj.get(p, 0) for p in st), 1) if proj else None,
            bench_pts=round(sum(pp.get(p, 0) for p in bench), 2),
            top=dict(player=name(top), pts=pp.get(top, 0)),
            swap=dict(bench=name(b), bench_pts=pp.get(b, 0), start=name(s), start_pts=pp.get(s, 0),
                      gain=round(gain, 2)) if gain > 0 else None)
        lineups[team[rid]] = dict(
            started=[dict(slot=slot, player=name(p), pts=pp.get(p, 0), proj=proj.get(p), line=line(p))
                     for p, slot in zip(st, slots) if p != "0"],
            bench=[dict(player=name(p), pos=info(p).get("position"), pts=pp.get(p, 0), line=line(p)) for p in bench])
    games = []
    for a, b in pairs(ms):
        if a["points"] < b["points"]:
            a, b = b, a
        games.append(dict(win=card[a["roster_id"]], lose=card[b["roster_id"]], margin=round(a["points"] - b["points"], 2)))

    # Trophies: the numbers are picked here; Claude only writes the jokes.
    awards = []

    def award(key, emoji, label, who, stat, vs=None):
        awards.append(dict(key=key, emoji=emoji, label=label, team=who, stat=stat, vs=vs))

    def swap_vs(sw):
        return sw and dict(a=sw["bench"], a_pts=sw["bench_pts"], a_tag="bench",
                           b=sw["start"], b_pts=sw["start_pts"], b_tag="started")

    def vs_proj(t):
        return f" ({t['pts'] - t['proj']:+.1f} vs proj)" if t.get("proj") else ""

    ranked = sorted(card.values(), key=lambda t: -t["pts"])
    blow, close = max(games, key=lambda g: g["margin"]), min(games, key=lambda g: g["margin"])
    award("blowout", "💣", "Biggest domination", blow["win"]["team"],
          f"by {blow['margin']:.2f} over {blow['lose']['team']}")
    award("high", "🥇", "Top score", ranked[0]["team"], f"{ranked[0]['pts']:.2f}{vs_proj(ranked[0])}")
    award("low", "💩", "Biggest loser", ranked[-1]["team"], f"{ranked[-1]['pts']:.2f}{vs_proj(ranked[-1])}")
    award("close", "🤏", "Closest game", close["win"]["team"], f"by {close['margin']:.2f} over {close['lose']['team']}")
    flips = [(g["lose"]["swap"]["gain"] - g["margin"], g) for g in games
             if g["lose"]["swap"] and g["lose"]["swap"]["gain"] > g["margin"]]
    if flips:
        by, g = min(flips, key=lambda x: x[0])
        award("heartbreaker", "💔", "Heartbreaker", g["lose"]["team"],
              f"lost by {g['margin']:.2f}, one swap from winning by {by:.2f}", swap_vs(g["lose"]["swap"]))
    best = max(ranked, key=lambda t: (t["eff"], t["pts"]))
    worst = min(ranked, key=lambda t: (t["eff"], t["pts"]))
    award("best_mgr", "🔥", "Best manager", best["team"], f"{best['eff']:g}% of max ({best['opt']:.2f})")
    award("worst_mgr", "🤡", "Worst manager", worst["team"], f"{worst['eff']:g}% of max ({worst['opt']:.2f})",
          swap_vs(worst["swap"]))
    deep = max(ranked, key=lambda t: t["bench_pts"])
    award("best_bench", "🪑", "Best bench", deep["team"], f"{deep['bench_pts']:.2f} on the bench")
    starts = [(m["players_points"].get(p, 0), p, m["roster_id"]) for m in ms for p in m["starters"] if p != "0"]
    pts, pid, rid = max(starts)
    award("mvp", "💪", "Best player", team[rid], f"{name(pid)} {pts:.2f}")
    pts, pid, m = max(((m["players_points"].get(p, 0), p, m) for m in ms for p in m["players"]
                       if p not in m["starters"]), key=lambda x: x[:2])
    sat = min(((m["players_points"].get(s, 0), s) for s, slot in zip(m["starters"], slots) if fits(pid, slot)),
              default=None)
    award("bench_mvp", "🛋️", "Bench MVP", team[m["roster_id"]], f"{name(pid)} {pts:.2f}",
          sat and dict(a=name(pid), a_pts=pts, a_tag="bench", b=name(sat[1]), b_pts=sat[0], b_tag="started"))
    diffs = [(p - proj[pid], p, pid, rid) for p, pid, rid in starts if proj.get(pid)]
    if diffs:
        d, p, pid, rid = max(diffs)
        award("over", "📈", "Overachiever", team[rid], f"{name(pid)} {p:.2f}, {d:+.1f} vs proj")
        d, p, pid, rid = min(diffs)
        award("under", "👎", "Underachiever", team[rid], f"{name(pid)} {p:.2f}, {d:+.1f} vs proj")
    lucky = min((g["win"] for g in games), key=lambda t: t["pts"])
    unlucky = max((g["lose"] for g in games), key=lambda t: t["pts"])
    award("lucky", "🍀", "Lucky", lucky["team"], f"won with the {ordinal(ranked.index(lucky) + 1)}-best score")
    award("unlucky", "😡", "Unlucky", unlucky["team"], f"lost with the {ordinal(ranked.index(unlucky) + 1)}-best score")

    # Gems: cross-roster comparisons a model won't reliably compute on its own.
    gems = []
    scores = sorted((t["pts"], t["team"]) for t in card.values())
    low_pts, low_team = scores[0]
    for t in card.values():
        beaten = [n for p, n in scores if p < t["bench_pts"] and n != t["team"]]
        if beaten:
            gems.append(f"{t['team']}'s bench ({t['bench_pts']:.2f}) outscored these whole lineups: {', '.join(beaten)}")
        elif t["team"] != low_team and low_pts - t["bench_pts"] < 10:
            gems.append(f"{t['team']}'s bench ({t['bench_pts']:.2f}) finished {low_pts - t['bench_pts']:.2f} "
                        f"behind {low_team}'s whole lineup ({low_pts:.2f})")
    low_rid = next(rid for rid, t in card.items() if t["team"] == low_team)
    low_starts = sorted(p for p, _, rid in starts if rid == low_rid)
    for pts, pid, rid in sorted(starts, reverse=True)[:3]:
        k, total = 0, 0.0
        while k < len(low_starts) and total + low_starts[k] < pts:
            total += low_starts[k]
            k += 1
        if k >= 2 and rid != low_rid:
            gems.append(f"{name(pid)} ({pts:.2f}, {team[rid]}) outscored {low_team}'s {k} lowest starters combined ({total:.2f})")
    game_of = {t["team"]: i for i, g in enumerate(games) for t in (g["win"], g["lose"])}
    near = min(((abs(a[0] - b[0]), a, b) for i, a in enumerate(scores) for b in scores[i + 1:]
                if game_of[a[1]] != game_of[b[1]]), default=None)
    if near:
        gems.append(f"{near[1][1]} ({near[1][0]:.2f}) and {near[2][1]} ({near[2][0]:.2f}) finished {near[0]:.2f} apart in different games")
    for g in games:
        if g["win"]["proj"] and g["win"]["proj"] == g["lose"]["proj"]:
            gems.append(f"{g['win']['team']} and {g['lose']['team']} were both projected for {g['win']['proj']}, "
                        f"then finished {g['margin']:.2f} apart")
    same_name = defaultdict(set)
    for m in ms:
        for p in m["players"]:
            same_name[name(p)].add((p, team[m["roster_id"]]))
        words = {re.sub(r"'s$", "", w).lower() for w in re.findall(r"[A-Za-z']{4,}", team[m["roster_id"]])}
        for mm in ms:
            for p in mm["players"]:
                if {info(p).get("first_name", "").lower(), info(p).get("last_name", "").lower()} & words:
                    how = "started" if p in mm["starters"] else "benched"
                    gems.append(f"{team[m['roster_id']]} shares a name with {info(p).get('full_name')}, who scored "
                                f"{mm['players_points'].get(p, 0):.2f} ({how}) for {team[mm['roster_id']]}")
    gems += [f"Two players named {n}, on " + " and ".join(sorted(t for _, t in v)) for n, v in same_name.items() if len(v) > 1]

    pickups = []
    for t in get(f"{api}/transactions/{week}", []):
        if t.get("status") == "complete":
            for pid, rid in (t.get("adds") or {}).items():
                m = next((m for m in ms if m["roster_id"] == rid), None)
                pickups.append(dict(team=team[rid], player=name(pid), bid=(t.get("settings") or {}).get("waiver_bid"),
                                    pts=m["players_points"].get(pid, 0) if m else 0,
                                    started=bool(m) and pid in m["starters"]))
    spend = max((x for x in pickups if x["bid"]), key=lambda x: x["bid"], default=None)
    if spend:
        award("big_spender", "💸", "Big spender", spend["team"],
              f"${spend['bid']} on {spend['player']}, {spend['pts']:.1f} pts")

    # Next week: pairings, projections for the lineups as currently set, and lineup PSAs.
    nxt = None
    upcoming = [m for m in get(f"{api}/matchups/{week + 1}", []) if m.get("matchup_id")]
    if upcoming:
        nproj = {x["player_id"]: (x.get("stats") or {}).get(pts_key) or 0
                 for x in get(PROJ.format(season, week + 1), [])}
        playing = {t for g in get(f"https://api.sleeper.com/schedule/nfl/regular/{season}", [])
                   if g.get("week") == week + 1 for t in (g.get("home"), g.get("away"))}
        lineup = {r["roster_id"]: [p for p in r.get("starters") or [] if p != "0"] for r in rosters}

        def projected(rid):
            return round(sum(nproj.get(p, 0) for p in lineup[rid]), 1) if nproj else None

        psa = [dict(team=team[rid], player=name(p), status=info(p).get("injury_status") or "no game")
               for rid, st in lineup.items() for p in st
               if info(p).get("injury_status") in HURT or (playing and info(p).get("team") not in playing)]
        nxt = dict(week=week + 1, psa=psa, games=[
            dict(a=team[a["roster_id"]], b=team[b["roster_id"]], a_proj=projected(a["roster_id"]),
                 b_proj=projected(b["roster_id"])) for a, b in pairs(upcoming)])

    return dict(league=lg["name"], season=season, week=week, playoff_teams=cfg.get("playoff_teams"), teams=teams,
                games=games, awards=awards, gems=gems, lineups=lineups, pickups=pickups, standings=standings,
                next=nxt)


LINES = {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["key", "line"],
                                    "properties": {"key": {"type": "string"}, "line": {"type": "string"}}}}
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["headline", "dek", "hero_value", "hero_caption", "pen_notes",
                       "award_lines", "game_lines", "preview_lines", "signoff"],
          "properties": {"headline": {"type": "string"}, "dek": {"type": "string"},
                         "hero_value": {"type": "string"}, "hero_caption": {"type": "string"},
                         "pen_notes": {"type": "array", "items": {"type": "string"}},
                         "award_lines": LINES, "game_lines": LINES,
                         "preview_lines": LINES, "signoff": {"type": "string"}}}


def write_copy(facts):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("::warning::ANTHROPIC_API_KEY is not set, so this week gets template copy instead of jokes.")
        return template_copy(facts)
    try:
        import anthropic

        msg = anthropic.Anthropic().beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            system=(ROOT / "prompt.md").read_text(),
            messages=[{"role": "user", "content": json.dumps(facts, ensure_ascii=False)}],
            extra_body={"fallbacks": "default",
                        "output_config": {"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}}},
        )
        if msg.stop_reason != "end_turn":
            raise RuntimeError(f"stop_reason={msg.stop_reason}")
        return dict(json.loads(next(b.text for b in msg.content if b.type == "text")), by=msg.model)
    except Exception as e:  # ponytail: any failure falls back to template copy so the numbers still ship
        print(f"::warning::Claude couldn't write the copy ({e}); using template copy.")
        return template_copy(facts)


def template_copy(f):
    a = {x["key"]: x for x in f["awards"]}
    lead = a.get("heartbreaker") or a["high"]
    return dict(by="template", headline=lead["label"].upper(), dek=f"{lead['team']}: {lead['stat']}.",
                hero_value=a["high"]["stat"].split(" ")[0], hero_caption=f"{a['high']['team']} led the week",
                pen_notes=[], award_lines=[], game_lines=[], preview_lines=[], signoff="")


def page(shell, f, c, url, data):
    e = html.escape
    who = {t["team"]: t for t in f["teams"]}
    lines = {k: {x["key"]: x["line"] for x in c.get(k) or []} for k in ("award_lines", "game_lines", "preview_lines")}
    notes = c.get("pen_notes") or []

    def note(i):
        return f'<p class="pen">{e(notes[i])}</p>' if i < len(notes) else ""

    def avatar(team_name, size=36):
        t = who.get(team_name, {})
        if t.get("avatar"):
            return f'<img class="av" src="{e(t["avatar"])}" alt="" width="{size}" height="{size}" loading="lazy">'
        return f'<span class="av">{e(team_name[:1].upper())}</span>'

    def face(team_name):
        t = who.get(team_name, {})
        sub = f"<small>{e(team_name)}</small>" if t.get("manager", team_name) != team_name else ""
        return f'<div class="face">{avatar(team_name)}<p>@{e(t.get("manager", team_name))}{sub}</p></div>'

    def vs(v):
        if not v:
            return ""
        return '<div class="vs">' + "".join(
            f'<span class="tag">{e(v[k + "_tag"])}</span><span>{e(v[k])}</span><b>{v[k + "_pts"]:.2f}</b>'
            for k in "ab") + "</div>"

    def line(kind, key, cls):
        text = lines[kind].get(str(key))
        return f'<p class="{cls}">{e(text)}</p>' if text else ""

    def row(team_name, pts, win=False):
        shown = "" if pts is None else f"{pts:.2f}"
        return f'<p class="g-row{" win" if win else ""}">{avatar(team_name, 24)}<span>{e(team_name)}</span><b>{shown}</b></p>'

    trophies = "".join(
        f'<article class="trophy"><p class="t-label"><span aria-hidden="true">{a["emoji"]}</span>{e(a["label"])}</p>'
        f'{face(a["team"])}<p class="t-stat">{e(a["stat"])}</p>{line("award_lines", a["key"], "t-line")}'
        f'{vs(a.get("vs"))}</article>' for a in f["awards"])
    games = "".join(
        f'<article class="game">{row(g["win"]["team"], g["win"]["pts"], True)}{row(g["lose"]["team"], g["lose"]["pts"])}'
        f'<p class="g-duel">{e(g["win"]["top"]["player"])} {g["win"]["top"]["pts"]:.1f} <i>vs</i> '
        f'{e(g["lose"]["top"]["player"])} {g["lose"]["top"]["pts"]:.1f}</p>{line("game_lines", i, "g-line")}</article>'
        for i, g in enumerate(f["games"], 1))

    upcoming = ""
    n = f.get("next")
    if n:
        rows = "".join(
            f'<article class="game">{row(g["a"], g["a_proj"])}{row(g["b"], g["b_proj"])}'
            f'{line("preview_lines", i, "g-line")}</article>' for i, g in enumerate(n["games"], 1))
        psa = "".join(f'<li><b>{e(x["player"])}</b> <span class="tag">{e(x["status"])}</span> '
                      f'in the {e(x["team"])} lineup</li>' for x in n["psa"])
        psa = f'<div class="psa"><p class="kicker">Lineup PSA</p><ul>{psa}</ul></div>' if psa else ""
        upcoming = (f'<section aria-labelledby="next-h"><div class="h-row"><h2 id="next-h">Week {n["week"]}</h2>'
                    f'<p class="kicker">Projected</p></div><div class="games">{rows}</div>{psa}</section>')

    cut = f.get("playoff_teams") or 0
    standings = "".join(
        f'<tr{" class=cut" if i == cut else ""}><td>{i}</td><th scope="row">{e(s["team"])}</th>'
        f'<td>{s["w"]}-{s["l"]}{"-" + str(s["t"]) if s["t"] else ""}</td><td>{s["pf"]:.2f}</td><td>{s["pa"]:.2f}</td>'
        f'<td>{s["all_play"]}</td><td>{s["luck"]:+.2f}</td><td>{s["bench_left"]:.1f}</td></tr>'
        for i, s in enumerate(f["standings"], 1))
    teams_wk = sorted((t for g in f["games"] for t in (g["win"], g["lose"])), key=lambda t: -t["eff"])
    report = "".join(
        f'<tr><th scope="row">{e(t["team"])}</th><td>{t["pts"]:.2f}</td><td>{t["opt"]:.2f}</td>'
        f'<td>{t["opt"] - t["pts"]:.2f}</td><td>{t["eff"]:g}%</td></tr>' for t in teams_wk)
    archive = "".join(
        f'<li><a href="{SITE}{d["facts"]["season"]}/{d["facts"]["week"]}/">Week {d["facts"]["week"]}: '
        f'{e(d["copy"]["headline"])}</a></li>' for d in reversed(data))

    body = f"""
<header class="mast"><p class="brand">Double<br>Dipper</p><p class="sticker">Wk {f["week"]}</p></header>
<section class="lead">
  <p class="kicker">{e(f["league"])} · Week {f["week"]}</p>
  <h1><span class="hl">{e(c["headline"])}</span></h1>
  <p class="dek">{e(c["dek"])}</p>
  <div class="hero"><p class="hero-num">{e(c["hero_value"])}</p><p class="hero-cap">{e(c["hero_caption"])}</p>{note(0)}</div>
</section>
<section aria-labelledby="tro-h"><div class="h-row"><h2 id="tro-h">Trophies</h2>{note(1)}</div><div class="trophies">{trophies}</div></section>
<section aria-labelledby="sb-h"><h2 id="sb-h">Scoreboard</h2><div class="games">{games}</div></section>
{upcoming}
<section aria-labelledby="nerd-h">
  <div class="h-row"><h2 id="nerd-h">Nerd Corner</h2>{note(2)}</div>
  <div class="scroll"><table class="standings"><caption>Standings</caption>
    <thead><tr><th scope="col">#</th><th scope="col">Team</th><th scope="col">W-L</th><th scope="col">PF</th><th scope="col">PA</th><th scope="col" title="Record if you played every team every week">All-play</th><th scope="col" title="Wins above what your all-play rate predicts">Luck</th><th scope="col" title="Season points left on the bench">Benched</th></tr></thead>
    <tbody>{standings}</tbody></table></div>
  <div class="scroll"><table class="report"><caption>Lineup report, week {f["week"]}</caption>
    <thead><tr><th scope="col">Team</th><th scope="col">Pts</th><th scope="col">Max</th><th scope="col">Left</th><th scope="col">Eff</th></tr></thead>
    <tbody>{report}</tbody></table></div>
</section>
<footer>
  {f'<p class="signoff">{e(c["signoff"])}</p>' if c.get("signoff") else ""}
  <ul class="archive">{archive}</ul>
  <p class="fine">Numbers from Sleeper. Jokes from Claude. Updates Tuesday nights.</p>
</footer>"""
    title = f"Double Dipper Wk {f['week']}: {c['headline']}"
    return (shell.replace("{{title}}", e(title)).replace("{{description}}", e(c["dek"]))
            .replace("{{url}}", e(url)).replace("{{body}}", body))


def render():
    shell = (ROOT / "template.html").read_text()
    data = [json.loads(p.read_text()) for p in sorted((ROOT / "weeks").glob("*.json"))]
    docs = ROOT / "docs"
    for d in data:
        f = d["facts"]
        url = f"{SITE}{f['season']}/{f['week']}/"
        out = docs / f["season"] / str(f["week"]) / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page(shell, f, d["copy"], url, data))
    if data:
        f, c = data[-1]["facts"], data[-1]["copy"]
        url = f"{SITE}{f['season']}/{f['week']}/"
        (docs / "index.html").write_text(page(shell, f, c, url, data))


def main():
    if sys.argv[1:] != ["render"]:
        facts = build_facts(os.environ.get("WEEK"))
        path = ROOT / "weeks" / f"{facts['season']}-{facts['week']:02d}.json"
        copy = json.loads(path.read_text())["copy"] if path.exists() else None
        if (not copy or os.environ.get("FRESH") == "true"
                or (copy.get("by") == "template" and os.environ.get("ANTHROPIC_API_KEY"))):
            copy = write_copy(facts)
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(dict(facts=facts, copy=copy), indent=1, ensure_ascii=False) + "\n")
    render()


if __name__ == "__main__":
    main()
