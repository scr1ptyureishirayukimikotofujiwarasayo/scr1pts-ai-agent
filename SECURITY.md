# Security policy

## API keys and private state

This project stores credentials only in `.env` (git-ignored) or the process
environment. It never writes keys into `config.json`, `mcp.json`, sessions,
logs or prompts.

Before opening a pull request, publishing the folder, or sharing an archive:

```bat
clean.bat --yes                :: Windows: keys -> blank, sessions/caches removed
```
```sh
./clean.sh --yes               # Linux / macOS / WSL
```

`mcp.json` uses `${GITHUB_TOKEN}` style placeholders; unset variables make the
harness skip that server instead of launching it with a broken token.

Never commit:

- `.env` or any `.env.*` file other than `.env.example`
- `sessions/*.json` (conversation contents)
- `.cache/` (model catalogue and any local caches)
- pasted tokens in `plugins/`, `skills/`, tests or `AGENTS.md`

## Reporting a vulnerability

Please use GitHub's **Report a vulnerability** (private security advisory)
feature on this repository rather than a public issue. Include reproduction
steps and the affected file/command.

## Scope

The harness runs with your user privileges and, by design, can execute Python,
project shell tooling, plugins and MCP stdio servers. Sandboxing is not provided;
only run it on repositories and with MCP servers you trust, and review
`plugins/` before enabling them.
