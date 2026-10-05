#!/usr/bin/env python3
"""Dev Boards board for Herdr — curses TUI pane.

Reads missions from app.devboards.ai agent API and renders the board live,
binding running missions to Herdr agent panes by agent name.

Config (optional): $HERDR_PLUGIN_CONFIG_DIR/devboards.env with
  DEVBOARDS_BASE_URL=...
  DEVBOARDS_API_KEY=ark_...
  DEVBOARDS_AGENT=...
Falls back to the inherited environment.

Keys: r refresh · ←/→ column · ↑/↓ select · o open app · q quit
"""
import curses
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

COLUMNS = ["inbox", "ready", "running", "review", "needs_human"]
REFRESH_S = 30

STATE = {"data": {}, "sel": 0, "cursor": 0, "last": 0.0, "err": None}


def load_config():
    env = dict(os.environ)
    cfg = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if cfg:
        path = os.path.join(cfg, "devboards.env")
        if os.path.exists(path):
            for line in open(path):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env


def api(env, path):
    base = env.get("DEVBOARDS_BASE_URL", "https://app.devboards.ai").rstrip("/")
    key = env.get("DEVBOARDS_API_KEY", "")
    if not key:
        raise RuntimeError("DEVBOARDS_API_KEY not set (put it in the plugin config dir)")
    req = urllib.request.Request(base + path, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def fetch(env):
    out = {}
    for col in COLUMNS:
        try:
            d = api(env, f"/api/agent/missions?column={col}&limit=50&agent={env.get('DEVBOARDS_AGENT', 'frodo')}")
            out[col] = d.get("missions", [])
        except urllib.error.HTTPError as e:
            out[col] = [{"id": "?", "title": f"HTTP {e.code}", "projectId": None}]
        except Exception as e:  # noqa: BLE001
            out[col] = [{"id": "?", "title": str(e)[:60], "projectId": None}]
    return out


def herdr_agents():
    """[(agent_name, status)] from the live herdr session."""
    herdr = os.environ.get("HERDR_BIN_PATH", "herdr")
    try:
        r = subprocess.run([herdr, "agent", "list", "--format", "json"],
                           capture_output=True, text=True, timeout=10)
        d = json.loads(r.stdout or "{}")
        out = []
        for a in d.get("agents", d.get("items", [])):
            name = a.get("name") or a.get("agent")
            st = a.get("status") or a.get("agent_status")
            if name:
                out.append((name, st))
        return out
    except Exception:  # noqa: BLE001
        return []


def col_color(col):
    return {"inbox": 4, "ready": 6, "running": 2, "review": 3, "needs_human": 1}.get(col, 7)


def draw(scr):
    curses.curs_set(0)
    scr.nodelay(True)
    scr.timeout(1000)
    cols = list(COLUMNS)
    agents = dict(herdr_agents())
    while True:
        now = time.time()
        if now - STATE["last"] > REFRESH_S or not STATE["data"]:
            try:
                STATE["data"] = fetch(load_config())
                STATE["err"] = None
            except Exception as e:  # noqa: BLE001
                STATE["err"] = str(e)[:70]
            STATE["last"] = now
            agents = dict(herdr_agents())

        scr.erase()
        h, w = scr.getmaxyx()
        scr.attron(curses.A_BOLD)
        scr.addnstr(0, 0, "  DEV BOARDS", w - 1)
        scr.attroff(curses.A_BOLD)
        info = f"refreshed {max(0, int(now - STATE['last']))}s ago" + (f" · {STATE['err']}" if STATE["err"] else "")
        scr.addnstr(0, max(0, w - len(info) - 2), info, w - 1)

        cw = max(20, w // len(cols))
        for ci, col in enumerate(cols):
            x = ci * cw
            attr = curses.color_pair(col_color(col)) | curses.A_BOLD
            scr.addnstr(1, x + 1, f"{col.upper()} ({len(STATE['data'].get(col, []))})", cw - 2, attr)
            scr.addnstr(2, x, "─" * (cw - 1), cw - 1, curses.color_pair(8))
            sel_col, sel_row = STATE["sel"], STATE["cursor"]
            for ri, m in enumerate(STATE["data"].get(col, [])):
                if 3 + ri >= h - 2:
                    scr.addnstr(h - 2, x + 1, "…", cw - 2)
                    break
                title = str(m.get("title") or m.get("id") or "?")
                who = m.get("agent") or m.get("assignee") or ""
                line = f"{'▸ ' if (ci == sel_col and ri == sel_row) else '  '}{title[:cw - 8]}"
                a = curses.color_pair(col_color(col))
                if ci == sel_col and ri == sel_row:
                    a |= curses.A_REVERSE
                scr.addnstr(3 + ri, x, line[: cw - 1], cw - 1, a)
                if who:
                    bound = "◉" if who in agents else "○"
                    scr.addnstr(3 + ri, x + cw - 6, f"{bound}{who[:4]}", 5, curses.color_pair(8))

        footer = "r refresh · ←→ column · ↑↓ select · o open app · q quit"
        scr.addnstr(h - 1, 0, footer[: w - 1], w - 1, curses.color_pair(8))
        scr.refresh()

        k = scr.getch()
        if k in (ord("q"), 27):
            return
        if k == ord("r"):
            STATE["last"] = 0
        elif k == curses.KEY_RIGHT:
            STATE["sel"] = min(len(cols) - 1, STATE["sel"] + 1)
            STATE["cursor"] = 0
        elif k == curses.KEY_LEFT:
            STATE["sel"] = max(0, STATE["sel"] - 1)
            STATE["cursor"] = 0
        elif k == curses.KEY_DOWN:
            cur = STATE["data"].get(cols[STATE["sel"]], [])
            STATE["cursor"] = min(len(cur) - 1, STATE["cursor"] + 1)
        elif k == curses.KEY_UP:
            STATE["cursor"] = max(0, STATE["cursor"] - 1)
        elif k == ord("o"):
            env = load_config()
            base = env.get("DEVBOARDS_BASE_URL", "https://app.devboards.ai")
            subprocess.Popen(["xdg-open", base],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def render_plain():
    """One-shot text snapshot (for logs, agents, and pipes)."""
    env = load_config()
    try:
        data = fetch(env)
    except Exception as e:  # noqa: BLE001
        print(f"devboards error: {e}")
        return 1
    agents = dict(herdr_agents())
    print(f"DEV BOARDS · refreshed {time.strftime('%Y-%m-%d %H:%M:%S')}")
    for col in COLUMNS:
        ms = data.get(col, [])
        print(f"\n== {col.upper()} ({len(ms)}) ==")
        for m in ms[:12]:
            who = m.get("agent") or m.get("assignee") or ""
            mark = f"{'◉' if who in agents else '○'}{who}" if who else ""
            print(f"  {str(m.get('title') or m.get('id') or '?')[:70]:<70} {mark}")
        if len(ms) > 12:
            print(f"  … +{len(ms) - 12} more")
    return 0


def main():
    if os.environ.get("HERDR_DEVBOARDS_PLAIN") == "1" or "--plain" in os.sys.argv:
        raise SystemExit(render_plain())
    os.environ.setdefault("TERM", os.environ.get("TERM") or "xterm-256color")
    curses.wrapper(main_curses)


def main_curses(scr):
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_RED, -1)
    curses.init_pair(2, curses.COLOR_GREEN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_CYAN, -1)
    curses.init_pair(6, curses.COLOR_MAGENTA, -1)
    curses.init_pair(7, curses.COLOR_WHITE, -1)
    curses.init_pair(8, curses.COLOR_WHITE, -1)
    draw(scr)


if __name__ == "__main__":
    main()
