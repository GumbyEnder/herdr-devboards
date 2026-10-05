# herdr-devboards

Your [Dev Boards](https://app.devboards.ai) missions, live and actionable in a [Herdr](https://herdr.dev) pane.

- All five lanes (inbox / ready / running / review / needs_human), auto-refresh every 30 s
- **Agent-pane binding**: running missions show ◉ when the assigned agent matches a live Herdr agent
- **Full worker loop from the pane**: claim, deliver (→ review), escalate (→ needs_human), quick-capture new missions (→ inbox)
- **Dispatch**: hand a ready/inbox mission straight to an idle Herdr agent pane — the board claims it, sends the full mission brief (objective / context / constraints / acceptance) as the agent's prompt, and tracks it as running
- **Mission detail** with the full history trail — operator feedback and bounce reasons included
- **Auto-heartbeat** for running missions whose agent has a live pane
- **Arrival notifications**: new review / needs_human items pop a Herdr notification (toggle with `a`)
- Zero dependencies (Python stdlib + curses)

## Install

```sh
herdr plugin install gumbyender/herdr-devboards
```

or for local development:

```sh
herdr plugin link /path/to/herdr-devboards
herdr plugin pane open --plugin devboards.board --entrypoint board
```

## Configuration

The plugin reads `DEVBOARDS_BASE_URL`, `DEVBOARDS_API_KEY`, and `DEVBOARDS_AGENT` from its environment. For a persistent setup, write them to the plugin config dir:

```sh
cat > "$(herdr plugin config-dir devboards.board)/devboards.env" <<'EOF'
DEVBOARDS_BASE_URL=https://app.devboards.ai
DEVBOARDS_API_KEY=ark_...
DEVBOARDS_AGENT=frodo
EOF
```

> Herdr-launched pane processes do not inherit your shell profile env — use the config dir.

## Keys

| key | action |
|-----|--------|
| `r` | refresh now |
| `←/→` `↑/↓` | navigate lanes / selection |
| `i` | mission detail + history trail |
| `c` | claim selected mission (inbox/ready → running) |
| `d` | deliver selected running mission (→ review) |
| `e` | escalate selected running mission (→ needs_human) |
| `n` | quick-capture a new mission (→ inbox) |
| `⏎` | dispatch to an idle Herdr agent (claims + sends the brief) |
| `a` | toggle arrival alerts |
| `o` | open app.devboards.ai |
| `q` | close |

## CLI actions

- `herdr plugin action invoke devboards.board.snapshot` — plain-text board dump for pipes and agent reads; also `python3 board.py --plain`.

## Notes

- Write verbs follow the Dev Boards agent API ACL: claim/heartbeat/deliver/escalate/ingest only. Column moves and board creation stay operator-only (by design).
- `◉/○` next to a running mission: the assigned agent does / doesn't match a live Herdr agent in the current session.

License: MIT.
