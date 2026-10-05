# herdr-devboards

Your [Dev Boards](https://app.devboards.ai) missions, live in a [Herdr](https://herdr.dev) pane.

- All five lanes (inbox / ready / running / review / needs_human), auto-refresh every 30 s
- **Agent-pane binding**: running missions show ◉ when the assigned agent name matches a live Herdr agent in your session
- One key (`o`) opens the app; `r` refreshes; arrows navigate
- Zero dependencies (Python stdlib + curses); works with any agent key that can read missions

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

## Keys

| key | action |
|-----|--------|
| `r` | refresh now |
| `←/→` | move between lanes |
| `↑/↓` | move selection |
| `o` | open app.devboards.ai |
| `q` | close |

## Notes

- Board visibility follows the agent key's ACL — you see the lanes your key can read.
- The `◉/○` marker next to a running mission means the assigned agent does / doesn't match a live Herdr agent in the current session.

License: MIT.
