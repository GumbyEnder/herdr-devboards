#!/usr/bin/env python3
"""Dev Boards board for Herdr — curses TUI pane.

Live board (read + write): claim / deliver / escalate / quick-capture missions,
auto-heartbeat for bound agents, dispatch a mission into a Herdr agent pane,
review-gate view with operator feedback, and arrival notifications.

Config (optional): $HERDR_PLUGIN_CONFIG_DIR/devboards.env with
  DEVBOARDS_BASE_URL=...
  DEVBOARDS_API_KEY=ark_...
  DEVBOARDS_AGENT=...
Falls back to the inherited environment.

Keys: r refresh · ←/→ lane · ↑/↓ select · i mission detail · c claim ·
      d deliver · e escalate · n quick-capture · Enter dispatch to agent ·
      a alert toggle · o open app · q quit
"""
import curses
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request

COLUMNS = ["inbox", "ready", "running", "review", "needs_human"]
REFRESH_S = 30
HEARTBEAT_S = 240

STATE = {
    "data": {}, "sel": 0, "cursor": 0, "last": 0.0, "err": None,
    "detail": None, "input": None, "input_label": None, "toast": None,
    "toast_until": 0, "alerts": True, "seen_keys": set(),
    "pick": None,
}


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


def _request(env, method, path, payload=None):
    base = env.get("DEVBOARDS_BASE_URL", "https://app.devboards.ai").rstrip("/")
    key = env.get("DEVBOARDS_API_KEY", "")
    if not key:
        raise RuntimeError("DEVBOARDS_API_KEY not set (put it in the plugin config dir)")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def api(env, path, payload=None):
    if payload is not None:
        return _request(env, "POST", path, payload)
    return _request(env, "GET", path)


def agent_name(env):
    return env.get("DEVBOARDS_AGENT") or (api(env, "/api/agent").get("agent"))


def fetch(env):
    out = {}
    who = env.get("DEVBOARDS_AGENT", "")
    for col in COLUMNS:
        try:
            d = api(env, f"/api/agent/missions?column={col}&limit=50&agent={who}")
            out[col] = d.get("missions", [])
        except urllib.error.HTTPError as e:
            out[col] = [{"id": "?", "title": f"HTTP {e.code}", "projectId": None}]
        except Exception as e:  # noqa: BLE001
            out[col] = [{"id": "?", "title": str(e)[:60], "projectId": None}]
    return out


def selected():
    col = COLUMNS[STATE["sel"]]
    ms = STATE["data"].get(col, [])
    if not ms:
        return None, col
    return ms[min(STATE["cursor"], len(ms) - 1)], col


def toast(msg):
    STATE["toast"] = msg
    STATE["toast_until"] = time.time() + 4


def herdr_agents():
    """[(name, status)] for live agents in this session."""
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


def herdr_notify(title, body=""):
    herdr = os.environ.get("HERDR_BIN_PATH", "herdr")
    try:
        subprocess.Popen([herdr, "notification", "show", "--position", "bottom-right",
                          title, "--body", body],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        pass


def agent_prompt(target, text):
    herdr = os.environ.get("HERDR_BIN_PATH", "herdr")
    return subprocess.run([herdr, "agent", "prompt", target, text, "--wait", "--timeout", "60000"],
                          capture_output=True, text=True, timeout=90)


def mission_brief(m):
    return (f"Mission {m['id']} — {m.get('title', '')}\n\n"
            f"Objective: {m.get('objective', '')}\n\n"
            f"Context: {m.get('context', '')}\n\n"
            f"Constraints: {m.get('constraints', '')}\n\n"
            f"Acceptance: {m.get('acceptance', '')}\n\n"
            f"When done: report a short delivery note (files touched / results). "
            f"If blocked, say what you need.")


# ---- actions -----------------------------------------------------------

def act_claim(env):
    m, col = selected()
    if not m or not str(m.get("id", "")).startswith("msn"):
        toast("Nothing selectable here")
        return
    try:
        api(env, f"/api/agent/missions/{m['id']}/claim", {"agent": agent_name(env)})
        toast(f"Claimed {m['id']}")
        refresh_now(env)
    except urllib.error.HTTPError as e:
        toast(f"Claim failed: HTTP {e.code}")
    except Exception as e:  # noqa: BLE001
        toast(f"Claim failed: {str(e)[:60]}")


def act_deliver(env):
    m, col = selected()
    if not m or not str(m.get("id", "")).startswith("msn"):
        toast("Nothing selectable here")
        return
    if col != "running":
        toast("Deliver works on a running mission")
        return
    STATE["input"] = {"kind": "deliver", "mission": m, "text": ""}
    STATE["input_label"] = f"Delivery summary for {m['id']} (Enter to send):"


def do_deliver(env, summary):
    m = STATE["input"]["mission"]
    try:
        api(env, f"/api/agent/missions/{m['id']}/deliver",
            {"agent": agent_name(env), "summary": summary, "artifacts": []})
        toast(f"Delivered {m['id']} → review")
        refresh_now(env)
    except urllib.error.HTTPError as e:
        toast(f"Deliver failed: HTTP {e.code}")
    except Exception as e:  # noqa: BLE001
        toast(f"Deliver failed: {str(e)[:60]}")


def act_escalate(env):
    m, col = selected()
    if not m or not str(m.get("id", "")).startswith("msn"):
        toast("Nothing selectable here")
        return
    if col != "running":
        toast("Escalate works on a running mission")
        return
    STATE["input"] = {"kind": "escalate", "mission": m, "text": ""}
    STATE["input_label"] = f"Question for {m['id']} (Enter to escalate):"


def do_escalate(env, question):
    m = STATE["input"]["mission"]
    try:
        api(env, f"/api/agent/missions/{m['id']}/escalate",
            {"agent": agent_name(env), "question": question})
        toast(f"Escalated {m['id']} → needs_human")
        refresh_now(env)
    except urllib.error.HTTPError as e:
        toast(f"Escalate failed: HTTP {e.code}")
    except Exception as e:  # noqa: BLE001
        toast(f"Escalate failed: {str(e)[:60]}")


def act_capture(env):
    STATE["input"] = {"kind": "capture", "mission": None, "text": ""}
    STATE["input_label"] = "New mission title (Enter to create → inbox):"


def do_capture(env, title):
    number = int(time.time() * 1000) % 10_000_000
    payload = {
        "number": number,
        "title": title,
        "body": "Captured from the Herdr board pane.",
        "html_url": env.get("DEVBOARDS_BASE_URL", "https://app.devboards.ai"),
        "state": "open",
        "labels": [{"name": "captured"}],
        "repository": {"full_name": "herdr-devboards/capture"},
    }
    # Route to the board the selected mission belongs to, else first visible board
    m, _ = selected()
    if m and m.get("projectId"):
        payload["projectId"] = m["projectId"]
    else:
        try:
            boards = api(env, "/api/agent/boards").get("boards", [])
            payload["projectId"] = boards[0].get("id") if boards else "proj_default"
        except Exception:  # noqa: BLE001
            payload["projectId"] = "proj_default"
    try:
        d = api(env, "/api/agent/ingest/github", payload)
        if d.get("created"):
            toast(f"Created {d['mission']['id']} → inbox")
        else:
            toast("Not created (dedupe?)")
        refresh_now(env)
    except urllib.error.HTTPError as e:
        toast(f"Capture failed: HTTP {e.code}")
    except Exception as e:  # noqa: BLE001
        toast(f"Capture failed: {str(e)[:60]}")


def act_dispatch(env):
    m, col = selected()
    if not m or not str(m.get("id", "")).startswith("msn"):
        toast("Nothing selectable here")
        return
    if col not in ("inbox", "ready"):
        toast("Dispatch claims from inbox/ready")
        return
    idle = [n for n, st in herdr_agents() if st in ("idle", "done", "unknown")]
    if not idle:
        toast("No idle Herdr agents to dispatch to")
        return
    STATE["pick"] = {"mission": m, "agents": idle, "idx": 0}
    toast("Pick agent: ↑/↓ + Enter, Esc cancels")


def do_dispatch(env, mission, target):
    try:
        api(env, f"/api/agent/missions/{mission['id']}/claim", {"agent": agent_name(env)})
    except Exception as e:  # noqa: BLE001
        toast(f"Claim failed: {str(e)[:50]} — dispatching anyway")
    try:
        r = agent_prompt(target, mission_brief(mission))
        if r.returncode == 0:
            toast(f"Dispatched {mission['id']} → {target}")
            herdr_notify("Mission dispatched", f"{mission.get('title', mission['id'])} → {target}")
        else:
            toast(f"Prompt failed ({r.returncode}): {(r.stderr or '').strip()[:50]}")
    except Exception as e:  # noqa: BLE001
        toast(f"Dispatch failed: {str(e)[:60]}")
    refresh_now(env)


def act_detail(env):
    m, col = selected()
    if not m or not str(m.get("id", "")).startswith("msn"):
        return
    detail = {k: m.get(k) for k in ("id", "title", "objective", "context",
                                    "constraints", "acceptance", "priority",
                                    "claimedBy", "progressNote", "delivery")}
    detail["history"] = []
    try:
        h = api(env, f"/api/agent/missions/{m['id']}/history").get("history", [])
        detail["history"] = [
            f"{time.strftime('%m-%d %H:%M', time.localtime(x.get('at', 0) / 1000))} "
            f"{x.get('actorName', '?')}: {x.get('fromColumn') or '·'} → {x.get('toColumn') or '·'}"
            + (f" — {x.get('note')}" if x.get("note") else "")
            for x in h[-8:]
        ]
    except Exception:  # noqa: BLE001
        pass
    STATE["detail"] = detail


# ---- heartbeat + arrivals ----------------------------------------------

def heartbeat_loop():
    while True:
        time.sleep(HEARTBEAT_S)
        try:
            env = load_config()
            names = {n for n, _ in herdr_agents()}
            for m in STATE["data"].get("running", []):
                who = (m.get("claimedBy") or "").replace("agent_", "")
                if who and who in names:
                    api(env, f"/api/agent/missions/{m['id']}/heartbeat",
                        {"agent": agent_name(env), "note": f"herdr pane active ({who})"})
        except Exception:  # noqa: BLE001
            pass


def check_arrivals(env):
    for col in ("review", "needs_human"):
        for m in STATE["data"].get(col, []):
            key = (col, m.get("id"))
            if key not in STATE["seen_keys"] and STATE["seen_keys"] and STATE["alerts"]:
                herdr_notify(f"{col.replace('_', ' ').title()}: {str(m.get('title', m.get('id')))[:60]}",
                             "Open the Dev Boards pane")
            STATE["seen_keys"].add(key)


# ---- rendering ---------------------------------------------------------

def refresh_now(env):
    try:
        STATE["data"] = fetch(env)
        STATE["err"] = None
        check_arrivals(env)
    except Exception as e:  # noqa: BLE001
        STATE["err"] = str(e)[:70]
    STATE["last"] = time.time()


def col_color(col):
    return {"inbox": 4, "ready": 6, "running": 2, "review": 3, "needs_human": 1}.get(col, 7)


def draw(scr):
    curses.curs_set(0)
    scr.nodelay(True)
    scr.timeout(1000)
    env = load_config()
    cols = list(COLUMNS)
    while True:
        now = time.time()
        if now - STATE["last"] > REFRESH_S or not STATE["data"]:
            refresh_now(env)

        scr.erase()
        h, w = scr.getmaxyx()

        if STATE["detail"]:
            draw_detail(scr, h, w)
            k = scr.getch()
            if k in (ord("i"), 27, ord("q")):
                STATE["detail"] = None
            continue

        if STATE["pick"]:
            draw_pick(scr, h, w)
            k = scr.getch()
            if k in (27, ord("q")):
                STATE["pick"] = None
            elif k == curses.KEY_UP:
                STATE["pick"]["idx"] = max(0, STATE["pick"]["idx"] - 1)
            elif k == curses.KEY_DOWN:
                STATE["pick"]["idx"] = min(len(STATE["pick"]["agents"]) - 1, STATE["pick"]["idx"] + 1)
            elif k in (10, curses.KEY_ENTER):
                m, target = STATE["pick"]["mission"], STATE["pick"]["agents"][STATE["pick"]["idx"]]
                STATE["pick"] = None
                do_dispatch(env, m, target)
            continue

        if STATE["input"] is not None:
            draw_input(scr, h, w)
            k = scr.getch()
            box = STATE["input"]
            if k == 27:
                STATE["input"] = None
            elif k in (10, curses.KEY_ENTER):
                text = box["text"].strip()
                STATE["input"] = None
                if text:
                    if box["kind"] == "deliver":
                        do_deliver(env, text)
                    elif box["kind"] == "escalate":
                        do_escalate(env, text)
                    elif box["kind"] == "capture":
                        do_capture(env, text)
            elif k in (curses.KEY_BACKSPACE, 127, 8):
                box["text"] = box["text"][:-1]
            elif 32 <= k < 127:
                box["text"] += chr(k)
            continue

        scr.attron(curses.A_BOLD)
        scr.addnstr(0, 0, "  DEV BOARDS", w - 1)
        scr.attroff(curses.A_BOLD)
        info = (f"refreshed {max(0, int(now - STATE['last']))}s ago"
                + (f" · {STATE['err']}" if STATE["err"] else "")
                + (" · alerts on" if STATE["alerts"] else " · alerts off"))
        scr.addnstr(0, max(0, w - len(info) - 2), info, w - 1, curses.color_pair(8))

        cw = max(20, w // len(cols))
        for ci, col in enumerate(cols):
            x = ci * cw
            attr = curses.color_pair(col_color(col)) | curses.A_BOLD
            scr.addnstr(1, x + 1, f"{col.upper()} ({len(STATE['data'].get(col, []))})", cw - 2, attr)
            scr.addnstr(2, x, "─" * (cw - 1), cw - 1, curses.color_pair(8))
            agents = dict(herdr_agents())
            for ri, m in enumerate(STATE["data"].get(col, [])):
                if 3 + ri >= h - 3:
                    scr.addnstr(h - 3, x + 1, "…", cw - 2)
                    break
                title = str(m.get("title") or m.get("id") or "?")
                who = (m.get("claimedBy") or "").replace("agent_", "")
                sel_here = (ci == STATE["sel"] and ri == STATE["cursor"])
                line = f"{'▸ ' if sel_here else '  '}{title[:cw - 8]}"
                a = curses.color_pair(col_color(col))
                if sel_here:
                    a |= curses.A_REVERSE
                scr.addnstr(3 + ri, x, line[: cw - 1], cw - 1, a)
                if who:
                    bound = "◉" if who in agents else "○"
                    scr.addnstr(3 + ri, x + cw - 6, f"{bound}{who[:4]}", 5, curses.color_pair(8))

        footer = ("r refresh · ←→ lane · ↑↓ select · i detail · c claim · d deliver · "
                  "e escalate · n capture · ⏎ dispatch · a alerts · o app · q quit")
        scr.addnstr(h - 2, 0, footer[: w - 1], w - 1, curses.color_pair(8))
        if STATE["toast"] and now < STATE["toast_until"]:
            scr.addnstr(h - 1, 0, STATE["toast"][: w - 1], w - 1,
                        curses.color_pair(3) | curses.A_BOLD)
        elif STATE["err"]:
            scr.addnstr(h - 1, 0, STATE["err"][: w - 1], w - 1, curses.color_pair(1))
        scr.refresh()

        k = scr.getch()
        if k in (ord("q"), 27):
            return
        if k == ord("r"):
            STATE["last"] = 0
        elif k == ord("c"):
            act_claim(env)
        elif k == ord("d"):
            act_deliver(env)
        elif k == ord("e"):
            act_escalate(env)
        elif k == ord("n"):
            act_capture(env)
        elif k == ord("a"):
            STATE["alerts"] = not STATE["alerts"]
            toast(f"alerts {'on' if STATE['alerts'] else 'off'}")
        elif k == ord("i"):
            act_detail(env)
        elif k in (curses.KEY_ENTER, 10):
            act_dispatch(env)
        elif k == ord("o"):
            subprocess.Popen(["xdg-open", env.get("DEVBOARDS_BASE_URL", "https://app.devboards.ai")],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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


def draw_detail(scr, h, w):
    d = STATE["detail"]
    scr.addnstr(0, 0, f"  MISSION {d['id']}", w - 1, curses.A_BOLD)
    y = 2
    for label, val in (("title", d.get("title")), ("objective", d.get("objective")),
                       ("context", d.get("context")), ("constraints", d.get("constraints")),
                       ("acceptance", d.get("acceptance")), ("claimed by", d.get("claimedBy")),
                       ("progress", d.get("progressNote")), ("delivery", d.get("delivery"))):
        if not val:
            continue
        scr.addnstr(y, 2, f"{label}:", 12, curses.color_pair(8))
        y += 1
        for chunk in str(val).split("\n"):
            while chunk and y < h - 2:
                scr.addnstr(y, 4, chunk[: w - 6], w - 6)
                chunk = chunk[w - 6:]
                y += 1
        y += 1
    if d.get("history") and y < h - 2:
        scr.addnstr(y, 2, "history:", 10, curses.color_pair(8))
        y += 1
        for line in d["history"]:
            if y >= h - 1:
                break
            scr.addnstr(y, 4, line[: w - 6], w - 6, curses.color_pair(8))
            y += 1
    scr.addnstr(h - 1, 0, "i / Esc back", w - 1, curses.color_pair(8))


def draw_pick(scr, h, w):
    p = STATE["pick"]
    scr.addnstr(h // 2 - 2, 2, f"Dispatch {p['mission']['id']} to:", w - 4, curses.A_BOLD)
    for i, a in enumerate(p["agents"]):
        attr = curses.A_REVERSE if i == p["idx"] else 0
        scr.addnstr(h // 2 - 1 + i, 4, f"{a}  ", w - 6, attr)


def draw_input(scr, h, w):
    scr.addnstr(h // 2 - 1, 2, STATE["input_label"], w - 4, curses.A_BOLD)
    scr.addnstr(h // 2 + 1, 2, "> " + STATE["input"]["text"], w - 4, curses.color_pair(3))
    scr.addnstr(h // 2 + 2, 2, "Enter send · Esc cancel", w - 4, curses.color_pair(8))


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
            who = (m.get("claimedBy") or m.get("agent") or "").replace("agent_", "")
            mark = f"{'◉' if who in agents else '○'}{who}" if who else ""
            print(f"  {str(m.get('title') or m.get('id') or '?')[:70]:<70} {mark}")
        if len(ms) > 12:
            print(f"  … +{len(ms) - 12} more")
    return 0


def main():
    if os.environ.get("HERDR_DEVBOARDS_PLAIN") == "1" or "--plain" in os.sys.argv:
        raise SystemExit(render_plain())
    os.environ.setdefault("TERM", os.environ.get("TERM") or "xterm-256color")
    threading.Thread(target=heartbeat_loop, daemon=True).start()
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
