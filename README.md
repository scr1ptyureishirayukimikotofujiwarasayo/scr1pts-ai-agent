# Python AI Agent

A small, hackable AI agent harness: one agent loop, pluggable model providers,
a tool registry, a skills folder and a plugin folder. **Pure standard library —
no `pip install` required** (Python 3.10+).

```
python ai agent/
  run.py                # entry point
  config.json           # defaults (provider, model, limits, paths)
  .env                  # API keys  (git-ignored)
  agent/
    config.py           # provider catalogue, .env loader, settings
    llm.py              # OpenAI-compatible chat client (streaming + tool calls)
    agent.py            # the loop: system prompt -> tools -> answer
    tools.py            # tool registry + JSON schemas
    essentials.py       # built-in tools (files, grep, shell, http, skills)
    skills.py           # skills loader (frontmatter markdown)
    plugins.py          # plugin loader + hook bus
    sessions.py         # session store (sessions/*.json)
    settings_spec.py    # settings catalogue: types, ranges, aliases
    secrets.py           # .env key parsing / rewriting / masking
    agentsmd.py          # AGENTS.md discovery + prompt injection
    mcp.py               # MCP client (stdio JSON-RPC) + tool bridging
    models.py            # /models catalogue fetch (+ key validation)
    modelsdev.py         # models.dev metadata (context, pricing, capabilities)
    shells.py            # bash/sh/cmd selection for run_command
    usage.py             # token estimates, context %, cost math
    picker.py            # arrow-key picker (model list) with plain fallback
    compact.py           # conversation compaction (model summary / digest)
    cleanup.py           # /clean implementation (keys, sessions, caches)
    lineedit.py         # raw-mode line editor + slash-command palette
    cli.py              # terminal UI, slash commands, saved sessions
    util.py             # colours, truncation, version
  skills/               # instruction packs, loaded on demand
  plugins/              # drop-in .py extensions (empty by default)
  docs/PLUGIN_GUIDE_FOR_AI.md  # guide for AI agents writing plugins
  examples/plugins/     # clock.py: template plugin, not auto-loaded
  tests/fixtures/       # test plugin used by the smoke suite
  clean.bat / clean.sh  # wipe keys, sessions, caches before sharing
  LICENSE               # MIT (put your name in it)
  SECURITY.md           # how to report issues / keep keys out of git
  .github/workflows/    # CI: offline smoke tests on Linux/Windows/macOS
  AGENTS.md             # optional project instructions for the agent
  mcp.json              # MCP server definitions
  sessions/             # saved conversations, one JSON per session
  tests/smoke_test.py   # offline end-to-end test
  tests/editor_pty_test.py  # interactive palette test (POSIX)
  tests/fake_mcp_server.py  # MCP server fixture for the smoke test
```

## Quick start

```bat
:: from a clone, or wherever you unpacked the folder
cd path\to\python-ai-agent
notepad .env                 :: paste a key, e.g. OPENROUTER_API_KEY=sk-or-...
python run.py
```

or double-click `run.bat` (`run.sh` on Linux/macOS/WSL).

No dependencies, no virtualenv required: any Python 3.10+ works. If your
`python` isn't on `PATH`, call it directly (e.g.
`C:\Python312\python.exe run.py`).

**No key yet? It still starts.** The harness launches without an API key and
shows `no API key for openrouter · /keys set openrouter <key> · /provider <name>`.
From inside the REPL you can set keys, pick a provider and pick a model:

```
> /keys set openrouter sk-or-v1-...
> /provider venice
> /keys set venice vn-...
> /model
> explain this codebase
```

Once inside, just type. Useful starts:

```
> summarise this project and suggest improvements
> add input validation to agent/llm.py and run the smoke test
```

To give the agent project rules, drop an `AGENTS.md` in the directory you run
it from (see [Project instructions](#project-instructions-agentsmd)).

Single-shot mode (great for scripting):

```bat
python run.py -p "list every tool and what it does"
python run.py --provider groq -m llama-3.3-70b-versatile -p "explain agent/agent.py"
```

Verify the harness without any API key:

```bat
python tests/smoke_test.py
```

On Linux/macOS/WSL you can also drive the real REPL in a pseudo-terminal to test
the palette, Tab completion and Ctrl+C handling:

```bash
python tests/editor_pty_test.py
```

## Providers

25 providers, every one speaking the OpenAI `chat/completions` format, so a
single client covers all of them. Pick with `--provider`, `AGENT_PROVIDER`,
`config.json`, `/provider` or `/settings provider` inside the REPL; `/providers`
lists them with the active one marked.

| provider      | env var                        | default base URL |
|---------------|--------------------------------|------------------|
| `openrouter`  | `OPENROUTER_API_KEY`           | `https://openrouter.ai/api/v1` |
| `opencode-go` | `OPENCODE_GO_API_KEY`          | `https://opencode.ai/zen/v1` (override with `OPENCODE_GO_BASE_URL`) |
| `opencode`    | `OPENCODE_API_KEY`             | `https://opencode.ai/zen/v1` |
| `openai`      | `OPENAI_API_KEY`               | `https://api.openai.com/v1` |
| `venice`      | `VENICE_API_KEY`               | `https://api.venice.ai/api/v1` |
| `anthropic`   | `ANTHROPIC_API_KEY`            | `https://api.anthropic.com/v1` |
| `google`      | `GEMINI_API_KEY`               | `https://generativelanguage.googleapis.com/v1beta/openai` |
| `groq`        | `GROQ_API_KEY`                 | `https://api.groq.com/openai/v1` |
| `deepseek`    | `DEEPSEEK_API_KEY`             | `https://api.deepseek.com/v1` |
| `mistral`     | `MISTRAL_API_KEY`              | `https://api.mistral.ai/v1` |
| `together`    | `TOGETHER_API_KEY`             | `https://api.together.xyz/v1` |
| `fireworks`   | `FIREWORKS_API_KEY`            | `https://api.fireworks.ai/inference/v1` |
| `xai`         | `XAI_API_KEY`                  | `https://api.x.ai/v1` |
| `cerebras`    | `CEREBRAS_API_KEY`             | `https://api.cerebras.ai/v1` |
| `perplexity`  | `PERPLEXITY_API_KEY`           | `https://api.perplexity.ai` |
| `moonshot`    | `MOONSHOT_API_KEY` / `KIMI_API_KEY` | `https://api.moonshot.ai/v1` |
| `zhipu`       | `ZHIPU_API_KEY` / `GLM_API_KEY` | `https://open.bigmodel.cn/api/paas/v4` |
| `dashscope`   | `DASHSCOPE_API_KEY` / `QWEN_API_KEY` | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` |
| `novita`      | `NOVITA_API_KEY`               | `https://api.novita.ai/v3/openai` |
| `nebius`      | `NEBIUS_API_KEY`               | `https://api.studio.nebius.ai/v1` |
| `hyperbolic`  | `HYPERBOLIC_API_KEY`           | `https://api.hyperbolic.xyz/v1` |
| `github`      | `GITHUB_TOKEN`                 | `https://models.github.ai/inference` |
| `ollama`      | *(none)*                       | `http://localhost:11434/v1` |
| `lmstudio`    | *(none)*                       | `http://localhost:1234/v1` |
| `custom`      | `CUSTOM_API_KEY`               | `CUSTOM_BASE_URL` |

Add or override a provider in `config.json` — no code changes:

```json
{
  "provider": "my-proxy",
  "model": "some-model",
  "providers": {
    "my-proxy": {
      "base_url": "https://example.com/v1",
      "base_url_env": "MY_PROXY_BASE_URL",
      "api_key_env": ["MY_PROXY_API_KEY"],
      "default_model": "some-model",
      "headers": {"X-Custom": "value"},
      "requires_key": true
    }
  }
}
```

`config.json` is deep-merged over the built-in defaults, so you can also just
tweak one field, e.g. `{"providers": {"openrouter": {"headers": {}}}}`.

Model ids change every few weeks; the `default_model` values are starting points.
Set `model` in `config.json`, pass `-m`, or use `/model <id>`. If a provider moves
its endpoint, override it without code: `VENICE_BASE_URL`, `PERPLEXITY_BASE_URL`,
`MOONSHOT_BASE_URL`, `ZHIPU_BASE_URL`, `DASHSCOPE_BASE_URL`, `NOVITA_BASE_URL`,
`NEBIUS_BASE_URL`, `HYPERBOLIC_BASE_URL`, `GITHUB_MODELS_BASE_URL` (see
`base_url_env` in the provider reference below).

> OpenCode plans differ per account. If your dashboard shows another endpoint,
> set `OPENCODE_GO_BASE_URL` (or `OPENCODE_BASE_URL`) in `.env`. Any
> OpenAI-compatible gateway works the same way.

### Model metadata from models.dev

Model ids alone are unhelpful. In addition to the provider's own `/models` list,
the harness reads [models.dev](https://models.dev) (the same catalogue OpenCode
uses) and annotates every entry with context window, price and capabilities.

```
> /model
Model · openrouter  (147/147)
↑/↓ select · Enter confirm · type to filter · Esc cancel
  anthropic/claude-sonnet-4.5   200k ctx · $3/$15 1M · tools · reasoning
❯ deepseek/deepseek-chat-v3.1  128k ctx · $0.28/$0.42 1M · tools
  openai/gpt-4.1                1M ctx · $2/$8 1M · tools · files

> /model info
deepseek/deepseek-chat-v3.1  ·  openrouter
  name         DeepSeek Chat v3.1
  family       deepseek
  context      128k
  max output   8k
  cost / 1M    input=$0.28  output=$0.42
  tool calls   yes
  reasoning    yes
  released     2025-08-19
```

- **Two sources, both useful:** `GET /models` says what *your key* can use (and
  validates the key); models.dev says what those models *are*. The picker/list
  merges them by id.
- **Offline-safe:** the catalogue is cached at `.cache/models_dev.json` for 24h
  (git-ignored) and read before the network, so the picker is instant and works
  without internet. A stale cache is still used if a refresh fails, with a dim
  warning.
- **Zero startup cost:** nothing is fetched until you run `/model` (or
  `/model list` / `/model info`). `/model refresh` re-fetches both the live list
  and the catalogue.
- **Graceful degradation:** providers without a `/models` endpoint (or behind a
  gateway that hides it) fall back to the models.dev id list, labelled
  "ids are unvalidated" — the picker still works.
- **Provider mapping:** aliases cover `together`→`togetherai`,
  `fireworks`→`fireworks-ai`, `moonshot`→`moonshotai`, `zhipu`→`zhipuai`,
  `dashscope`→`alibaba`, `novita`→`novita-ai`, `opencode-go`→`opencode-go`
  (falling back to `opencode`), plus exact ids for the rest. Providers genuinely
  absent from models.dev (`github`, `hyperbolic`) just get no annotations —
  everything else keeps working.
- **Overrides:** `MODELS_DEV_URL` (mirror or test server), `MODELS_DEV_CACHE`
  (cache path), `MODELS_DEV_TTL` (seconds, default 86400).

### Picking a model

`/model` validates the key by fetching the provider's model catalogue, then opens
a picker: **↑/↓ to move, Enter to choose, type to filter, Esc to cancel**. The
current model is marked `*`.

```
> /model
Model · openrouter  (147/147)
↑/↓ select · Enter confirm · type to filter · Esc cancel
  anthropic/claude-sonnet-4.5
❯ anthropic/claude-opus-4.1 *
  deepseek/deepseek-chat-v3.1
  ...
model -> anthropic/claude-opus-4.1
```

- **Fetching** is a `GET {base_url}/models` with the provider's auth headers, so
  an invalid key fails immediately with `API key rejected (HTTP 401)` instead of
  surfacing later during a chat turn. Results are cached per provider for the
  session (`/model refresh` re-fetches).
- **Keys are checked on write too:** `/keys set <provider> <key>` saves the key,
  **switches to that provider**, probes the catalogue and reports
  `key accepted · N models`. `/keys check [provider|all]` verifies existing keys
  and prints a per-provider ✓/✗ with model counts.
- **`/provider <name>`** validates the new key and pre-loads its catalogue, so
  `/model` afterwards is instant.
- **Variants:** `/model list` prints the catalogue without a picker,
  `/model <id>` sets directly, `/models` is an alias for `/model`, and
  `/model refresh` discards the cache.
- **No TTY** (pipes, IDE runners) prints the catalogue and accepts a number or id
  instead of using arrow keys, so `/model` still works in scripts.
- **No `/models` endpoint or a blocked one** (local gateways, Cloudflare/WAF):
  you get a yellow `catalogue unavailable (HTTP 403)` warning, the list falls
  back to models.dev ids, and `/model <id>` still works. A 403 is **not**
  treated as an invalid key — only 401 is.
- **Cloudflare/WAF providers** (e.g. OpenCode): requests send an explicit
  `User-Agent: python-ai-agent/<version>`. Python's default `Python-urllib` UA
  is rejected with `Error 1010: Access denied` (HTTP 403), which previously
  looked like a bad key. With the explicit UA the same endpoint responds
  normally.

## Configuration (`config.json`)

Every key is optional: `config.json` is deep-merged over the built-in defaults,
so a partial file is normal and `/settings save` writes the merged result back.
Unknown keys are preserved.

### Agent behaviour

| key | type | default | meaning |
|-----|------|---------|---------|
| `provider` | string | `"openrouter"` | active provider (key of the `providers` map) |
| `model` | string | `""` | model id; empty = the provider's `default_model` |
| `temperature` | number 0-2 | `0.7` | sampling temperature |
| `max_tokens` | int | `4096` | max tokens per reply |
| `max_steps` | int | `32` | max model↔tool round trips per turn |
| `compact_keep_turns` | int | `4` | recent user turns kept verbatim by `/compact` |
| `stream` | bool | `true` | stream tokens to the terminal |
| `timeout` | int (s) | `180` | timeout per model request |
| `confirm_shell` | bool | `true` | ask before dangerous tools (`run_command`, `write_file`, `edit_file`) |
| `auto_save` | bool | `true` | save the session on exit |
| `show_tool_results` | bool | `true` | print one line per tool result |
| `show_usage` | bool | `true` | print context/tokens/cost after every turn |
| `show_reasoning` | bool | `false` | stream the model's reasoning text, dimmed |
| `verbose` | bool | `false` | print latency + token usage |
| `system_prompt` | string | `""` | custom system prompt; empty = built-in prompt |
| `mode` | `"plan"` \| `"build"` | `"build"` | start in read-only planning mode or full build mode |
| `shell` | `auto` \| `bash` \| `sh` \| `cmd` | `"auto"` | shell used by `run_command`; `auto` prefers bash |
| `reasoning_effort` | `auto` \| `minimal` \| `low` \| `medium` \| `high` | `"auto"` | thinking effort for reasoning models |

### Paths

| key | type | default | meaning |
|-----|------|---------|---------|
| `workspace` | path | `"."` | root for relative tool paths; also where AGENTS.md is read |
| `skills_dir` | path | `"skills"` | skills folder (`SKILL.md` packs) |
| `plugins_dir` | path | `"plugins"` | plugin folder (`.py` tools + hooks) |
| `sessions_dir` | path | `"sessions"` | saved conversations |
| `env_file` | path | `".env"` | where `/keys` reads and writes API keys |
| `mcp_config` | path | `"mcp.json"` | MCP server definitions |
| `mcp_enabled` | bool | `true` | start MCP servers on launch |
| `mcp_timeout` | int (s) | `30` | `initialize` / `tools/list` / `tools/call` timeout |

Relative paths are resolved against the current working directory (the folder you
launch `run.py` from). `/settings save` stores them relative to the project when
possible, absolute otherwise.

### `providers` — adding or overriding a provider

```json
{
  "provider": "my-proxy",
  "model": "some-model",
  "providers": {
    "my-proxy": {
      "base_url": "https://example.com/v1",
      "base_url_env": "MY_PROXY_BASE_URL",
      "api_key_env": ["MY_PROXY_API_KEY"],
      "default_model": "some-model",
      "headers": {"X-Custom": "value"},
      "requires_key": true
    }
  }
}
```

| provider field | type | required | meaning |
|----------------|------|----------|---------|
| `base_url` | string | yes* | API root, without `/chat/completions` |
| `base_url_env` | string | no | env var that overrides `base_url` |
| `api_key_env` | string or list | yes* | env var(s) holding the key, first match wins |
| `api_key` | string | no | inline key (prefer `.env` / `/keys`) |
| `default_model` | string | no | model used when `model` is empty |
| `headers` | object | no | extra headers on every request |
| `requires_key` | bool | no (`true`) | whether a missing key is an error; `false` for local servers |

\* Provide `base_url` + `api_key_env`, or point `base_url_env` at it. A provider
enabled in config still needs its key present (or `requires_key: false`);
`/keys` shows which are set.

### Environment overrides

`AGENT_PROVIDER`, `AGENT_MODEL` override `config.json`; CLI flags override
everything:

```
CLI flags  →  AGENT_PROVIDER / AGENT_MODEL  →  config.json  →  built-in defaults
```

## Project instructions: `AGENTS.md`

If you put an `AGENTS.md` in the directory you work in, the agent follows it. It
is loaded into the system prompt at startup and treated as **binding project
instructions** (conventions, commands, forbidden actions), not as background
reading.

```
C:\my-project\
  AGENTS.md          <- the agent reads this when run here (or below)
  src\
  tests\
```

- **Where it looks:** `AGENTS.md` in the workspace, then each parent directory
  up to the file-system root (nearest first). Run the agent from a subfolder and
  the repo-root `AGENTS.md` still applies. `agents.md` / `Agents.md` / `AGENT.md`
  are accepted too.
- **Size limits:** 20,000 chars per file, 40,000 chars total; overflow is
  truncated with a marker.
- **Multiple files:** all discovered files are included, nearest first, each
  labelled with its path.
- **Nested folders / monorepos:** use the `read_agents_md` tool (the model calls
  it automatically when it needs rules for a subdirectory), e.g.
  `read_agents_md(path="packages/web")`.
- **Live reload:** edit the file, then `/agents reload` (or `/settings reload`).

```
> /agents
Project instructions  ·  workspace C:\my-project
 · C:\my-project\AGENTS.md (812 chars)
/agents reload re-reads them · now in the system prompt
```

Example `AGENTS.md`:

```markdown
# AGENTS.md

## Commands
- Install: `uv sync`
- Test: `uv run pytest -q` (must pass before any commit)
- Lint: `uv run ruff check .`

## Conventions
- Python 3.12, type hints everywhere, no `any` in public APIs.
- Keep modules under 400 lines; put shared helpers in `src/util/`.
- Never edit files under `generated/`; regenerate with `make gen`.

## Safety
- Do not touch `prod/` or run anything with `--apply` without asking.
```

`/settings workspace <path>` and `-w <path>` change which directory is treated as
the workspace, and therefore which `AGENTS.md` applies. The file's contents are
instructions to the model: only run the agent in repositories you trust.

## Modes: plan (read-only) and build

Two execution modes, switched with `/mode`, the `--mode` flag, or `mode` in
`config.json`. The prompt shows which one is active (`plan> ` vs `> `).

| | **plan** (read-only) | **build** (default) |
|---|---|---|
| read/search files, `grep`, `read_agents_md`, skills | yes | yes |
| `todo_write`, GET/HEAD `http_request` | yes | yes |
| MCP tools annotated `readOnlyHint` | yes | yes |
| `write_file`, `edit_file`, `run_command`, `run_python` | **blocked** | yes |
| MCP tools without the annotation, POST/PUT requests | **blocked** | yes |
| confirmation prompts | n/a (nothing mutates) | `y/n/always` for `!` tools |

Plan mode is enforced twice, not just described to the model:

1. **Schemas are filtered** — mutating tools are not sent to the model at all, so
   planning turns stay focused and cheap.
2. **Calls are refused** — if a model invents a tool call anyway (or a tool name
   arrives from history), the agent returns
   `Blocked by plan mode (read-only): ...` as the tool result and nothing runs.

The system prompt gains a `## Mode: PLAN (read-only)` section telling the model
to investigate, then deliver a plan with real file paths, ordered steps, risks
and verification commands, and to stop and ask for `/mode build` before changing
anything.

```
> /mode plan
mode -> plan · read-only: writes, shell and run_python are blocked
plan> refactor the session store to be async
  -> read_file ...
  ... plan only, no edits ...
plan> /mode build
mode -> build · full access restored
> go ahead
```

- `/mode` with no argument prints the current mode.
- The mode is per session; `/settings save` persists it as the next default.
- `--mode plan` is handy for review/audit runs: `python run.py --mode plan -p "review this diff"`.
- MCP servers control their own annotation: only tools with
  `annotations.readOnlyHint: true` are usable in plan mode, so an unannotated
  filesystem-write tool is blocked by default. Plugin tools can opt in with
  `ctx.tool(..., read_only=True)`.

### Thinking effort (`/think`)

Reasoning models trade latency and tokens for depth. Set that budget explicitly:

```
> /think
thinking effort: auto  ·  openrouter/anthropic/claude-sonnet-4.5
this model is marked reasoning-capable in models.dev
set with /think minimal|low|medium|high · auto = provider default

> /think high
thinking effort -> high · applies to reasoning models · /settings save to persist
```

- Levels: `auto` (default, send nothing), `minimal`, `low`, `medium`, `high`.
- Wire format is provider-shaped: OpenRouter gets
  `"reasoning": {"effort": "high"}` (its `minimal` maps to `low`, which it
  doesn't document); everyone else gets `"reasoning_effort": "high"`.
- Support is read from models.dev's `reasoning` flag: `/think` tells you whether
  the current model is marked reasoning-capable. If it isn't, the harness still
  tries, and a `400` mentioning reasoning is retried **once without the
  parameter**, remembered per provider — so a wrong guess can't break the turn.
- The banner shows `thinking: high` whenever it isn't `auto`, and context/token/
  cost lines reflect the larger replies automatically.
- **Reasoning is streamed, not silent.** While the model reads/plans you see a
  dim `…`; as soon as reasoning tokens arrive it becomes ` thinking…`. Set
  `/settings show_reasoning on` to stream the reasoning text itself (dimmed,
  separate line, terminal only).
- Persist with `/settings save`; also available as `/settings reasoning_effort`
  and as a hand-edited `config.json` key. Aliases: `/reasoning`, `/effort`,
  `/thinking`.

## Python-first execution

The harness pushes Python as the default way to get things done, because it is
the most reliable language for most models and behaves identically on Windows
and POSIX — no bash-isms, no quoting differences.

- **`run_python`** runs a snippet with the same interpreter as the agent, in the
  workspace, with `timeout`, optional `args`, stdout/stderr and exit code:

  ```python
  run_python(code="import json,pathlib\nprint(len(list(pathlib.Path('.').glob('**/*.py'))))")
  ```

- **`run_command`** is for the project's *own* tooling: `git`, `make`, `npm test`,
  `uv run pytest`, `docker compose ...`. The tool description says so, and the
  system prompt repeats it. It supports bash explicitly — see
  [Shell selection](#shell-selection-run_command) below.
- The system prompt (`## Tooling: Python first`) tells the model to use
  `run_python` for computation, file/JSON/CSV processing, HTTP and quick checks,
  to write one script instead of chaining shell steps, to use `pathlib`, and to
  print explicit output.
- A task that needs several shell steps gives one Python script rather than
  `cd x && ... && ...`, which keeps the transcript small and the result
  verifiable.
- **`run_python` is listed first in the tool schema** (priority ordering), ahead
  of `run_command`, because models bias toward tools they see earlier. The
  description of `run_command` also redirects scripting work to it, and the
  system prompt forbids `python -c ...`/heredocs through the shell tool.

### Shell selection (`run_command`)

`npm`, `make` and `git` often expect a POSIX shell. `run_command` accepts a
`shell` argument and defaults to `auto`, which prefers bash when it is installed:

| shell | behaviour |
|---|---|
| `auto` (default) | bash if available, otherwise the platform shell |
| `bash` | force bash — Git Bash on Windows, `/bin/bash` on POSIX; clear error with install hint if missing |
| `sh` | native POSIX shell (`shell=True`); rejected on Windows |
| `cmd` | `cmd.exe`; rejected on POSIX |

```python
run_command(command="npm test", shell="auto")
run_command(command="make -j4 && git status", shell="bash")
run_command(command="dir /b", shell="cmd")        # Windows native escape hatch
```

Details:

- **Detection order:** `AGENT_BASH` → `bash` on `PATH` → Git's usual install
  locations (`%ProgramFiles%\Git\bin\bash.exe`,
  `%LOCALAPPDATA%\Programs\Git\bin\bash.exe`). Git Bash is preferred on
  Windows and is started as a login shell (`-lc`) so `/usr/bin`, `npm`, `make`
  and friends are on `PATH`; POSIX bash uses `-c`.
- The chosen shell is printed in the result header, e.g.
  `[exit 0 · bash]`, so a failing `npm` script tells you what ran it.
- Change the default with `"shell"` in `config.json`, `/settings shell bash`, or
  `AGENT_SHELL=bash` (values: `auto`, `bash`, `sh`, `cmd`). `AGENT_BASH` points
  at a specific binary (e.g. a non-standard Git install).
- WSL's `bash.exe` is **not** auto-selected — it runs a different filesystem
  namespace, so Windows paths in `cwd`/tool arguments would not line up. Use Git
  Bash, or pass an explicit `AGENT_BASH` that points at a compatible binary.
- `run_python` never goes through a shell, so Python work stays immune to all of
  this.

## Settings at runtime (`/settings`)

Everything in the table above can be inspected and changed without restarting,
and written back to `config.json` when you like the result.

```
> /settings                       # every setting, current value, short description
> /settings temperature           # one setting, accepted values, providers for `provider`
> /settings temperature 0.2       # validate + apply immediately
> /settings stream off            # booleans accept on/off, true/false, yes/no, 1/0
> /settings save                  # persist to config.json (keeps unrelated keys)
> /settings reset temperature     # back to the value in config.json (or the default)
> /settings reset all             # re-read config.json from disk
> /settings reload                # same as reset all
> /settings path                  # which config file is in use
```

| key | type | effect of changing it live |
|-----|------|---------------------------|
| `provider` | provider name | rebuilds the client, resets model to that provider's default |
| `model` | text | applies to the next request, transcript kept |
| `temperature` | 0-2 | next request |
| `max_tokens` | 1-200000 | next request |
| `max_steps` | 1-1000 | next turn's tool loop |
| `compact_keep_turns` | 1-100 | default `/compact` verbatim window |
| `stream` | on/off | next request |
| `timeout` | 1-3600 s | next request |
| `confirm_shell` | on/off | confirmation prompt for dangerous tools |
| `auto_save` | on/off | saving on exit |
| `show_tool_results` | on/off | per-tool result lines in the terminal |
| `show_usage` | on/off | post-turn context/token/cost line |
| `show_reasoning` | on/off | stream reasoning text (terminal only) |
| `verbose` | on/off | latency + token stats |
| `workspace` | path | file/shell tools resolve here immediately |
| `skills_dir` | path | rescans skills and rebuilds the registry |
| `plugins_dir` | path | reloads plugins (new hooks replace old ones) |
| `sessions_dir` | path | switches the session store |
| `system_prompt` | text | replaces the prompt (empty string = built-in) |
| `mode` | plan/build | read-only, or restored full access |
| `shell` | auto/bash/sh/cmd | how the next `run_command` is executed |
| `reasoning_effort` | auto/minimal/low/medium/high | thinking effort for reasoning models |
| `env_file` | path | where `/keys` reads/writes API keys |
| `mcp_config` | path | reconnect MCP servers from this file |
| `mcp_enabled` | on/off | connect MCP servers at startup |
| `mcp_timeout` | 1-600 s | MCP initialize/list timeout |

Short aliases work too: `temp`, `tokens`, `steps`, `confirm`, `autosave`,
`cwd`/`dir`, `skills`, `plugins`, `sessions`, `prompt`. `/config` and `/set` are
aliases for `/settings`.

Values are validated (`temperature 9` is refused, `stream maybe` is refused) and
invalid input never changes anything. `/settings save` merges into the existing
file, so your `providers` block and unknown keys survive.

## Context, tokens and cost

After every turn the harness prints where the conversation stands:

```
  ctx 21.4% 42.8k/200k · session 84.2k tok · ~$0.0123
```

- **Context** = tokens currently in the window (system prompt + messages + tool
  schemas + last reply). The percentage comes from the active model's
  `limit.context` in models.dev; if the model isn't catalogued you still get the
  token count, just no percentage.
- **Session tokens** = cumulative input + output tokens billed this
  conversation, across all models used.
- **Cost** is estimated from models.dev `cost.input` / `cost.output`
  (USD per 1M tokens), summed per model so switching models mid-session stays
  accurate. Cache-read/write discounts are **not** modelled, so treat it as an
  upper-bound estimate.
- The number is exact when the provider reports `usage`; otherwise (e.g. a
  gateway that omits it in streaming) it's a characters/4 estimate and the line
  ends with `est.`. Streaming requests ask for
  `stream_options.include_usage`, and fall back automatically if a provider
  rejects that field.
- At ≥85% of the context window a `/compact` hint appears.

```
> /usage
Usage  ·  openrouter/anthropic/claude-sonnet-4.5
  context      42.8k / 200k  (21.4%)
  last request in 39.1k · out 3.7k
  session      in 78.4k · out 5.8k · 6 request(s)
  est. cost    $0.0123  (models.dev pricing, cache discounts ignored)
  by model
    openrouter/anthropic/claude-sonnet-4.5: 6 req · 78.4k in / 5.8k out · $0.0123
```

`/usage` (`/cost`, `/tokens`, `/context`) loads the models.dev catalogue if it
isn't cached yet, so the first call may take a moment. Toggle the per-turn line
with `show_usage` in `config.json`, `/settings show_usage off`, or
`AGENT_OFFLINE=1` (which also skips the background catalogue fetch).

Usage and cost are saved with sessions, so `/switch`-ing back restores the
running totals; `/clear` and `/new` reset them.

### Streaming output

Replies stream token-by-token instead of appearing all at once:

- a dim `…` is shown while waiting for the first token (erased as soon as text or
  a tool call arrives, so it never litters the transcript),
- reasoning models show ` thinking…` instead of sitting silent for half a minute,
- tool calls print as `-> tool`, results as `<- tool` (suppress results with
  `show_tool_results`),
- the context/tokens/cost line follows the reply on its own line.

`/clear` (alias `/cls`) now also wipes the visible screen and scrollback
(`ESC[2J ESC[3J ESC[H`, TTY only) and reprints a compact header, so a cleared
conversation doesn't leave old output above it. Piped/non-TTY output is never
polluted with escape codes.

## Built-in tools

`read_file`, `write_file`, `edit_file` (exact-snippet replace), `list_dir`,
`search_files` (glob), `grep` (regex, `path:line: text`), `run_command`
(shell, timeout, combined stdout/stderr), `http_request`, `todo_write`,
`run_python` (preferred execution path), `use_skill`, `read_agents_md`
(project instructions for a subdirectory).
Plugin tools (e.g. `now`) and MCP tools (`mcp__server__tool`) are added to the
same registry.

`/tools` marks dangerous tools with `!` and read-only tools with `r`
(`r` = usable in plan mode). With `confirm_shell: true` the agent asks
`y/n/always` before running dangerous tools. `--yes` (or `/verbose`-style config change via
`"confirm_shell": false`) auto-approves. File tools are rooted at `workspace`
but absolute paths are still allowed — this is your machine, not a sandbox.

## Skills

A skill is markdown instructions the model loads **only when relevant**.
`skills/<name>/SKILL.md` or a single `skills/<name>.md`:

```markdown
---
name: deploy
description: How to deploy to production. Use when the user says deploy/release.
---

# Deploy

1. `run_command("python -m pytest -q")` — never deploy red tests.
2. ...
```

Only `name` + `description` go into the system prompt (cheap). The full body is
returned by the `use_skill` tool on demand, so hundreds of skills cost nothing
until used. Support files (`skills/deploy/scripts/...`) are referenced by path.

Two examples ship in `skills/`: `code-review` and `git-commit`.

## Plugins

Drop a `.py` file (or a package with `__init__.py`) into `plugins/`. A broken
plugin is reported and skipped — it can't take the agent down.

```python
NAME = "weather"
DESCRIPTION = "Adds a weather lookup"

def register(ctx):
    @ctx.tool(
        "get_weather",
        "Current weather for a city.",
        {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
        dangerous=False,          # dangerous=True => confirmation prompt
    )
    def get_weather(city: str) -> str:
        return f"{city}: 21C, clear"   # str, dict or list; errors are shown to the model

    ctx.hook("on_turn_end", lambda text: ctx.log(f"turn finished ({len(text)} chars)"))
```

`ctx` gives you: `ctx.tool(name, description, schema, dangerous=False)`
(decorator, auto-namespaced on name clashes), `ctx.hook(event, fn)`,
`ctx.settings`, `ctx.workspace`, `ctx.root` (plugins folder), `ctx.registry`,
`ctx.log(msg)`, and `ctx.plugin` metadata.

Hooks and their arguments:

| hook | called with | special behaviour |
|------|-------------|-------------------|
| `on_start` | `agent` | after everything is wired |
| `on_system_prompt` | `agent` | return a string to append to the prompt |
| `on_user_message` | `text` | return a string to replace the message |
| `on_request` | `messages`, `tools`, `agent` | mutate the outgoing request in place |
| `on_response` | `completion`, `agent` | mutate content/reasoning/tool_calls |
| `on_tool_call` | `name`, `arguments` | dict replaces args; **str/False vetoes** the call |
| `on_tool_result` | `name`, `result`, `is_error` | return a string to replace the result |
| `on_assistant_message` | `text` | observe only |
| `on_turn_end` | `text` | observe only |
| `on_session_change` | `kind`, `name`, `agent` | `new` / `switch` / `clear` |
| `on_model_change` | `provider`, `model`, `agent` | after provider/model switches |
| `on_mode_change` | `mode`, `agent` | `plan` / `build` |
| `on_compact` | `result`, `agent` | after `/compact` |
| `on_shutdown` | — | before exit |

See `examples/plugins/clock.py` for a working example — copy it into
`plugins/` to enable it (`/tools` then shows `now`). A fresh checkout has an
empty `plugins/` folder, so the banner says `plugins: 0`.

Plugins can do more than add tools:

```python
def register(ctx):
    @ctx.command("deploy", args="[env]", description="ship it")   # slash command
    def deploy(arguments):
        print("deploying to", arguments or "staging")

    ctx.prompt("## House rules\nAlways run tests before committing.")  # prompt section

    ctx.hook("on_request", lambda messages, **_: messages.append(  # request shaping
        {"role": "user", "content": "Note: the user is on Windows."}
    ))
```

Plugin commands appear in the `/` palette and `/help`; hooks can rewrite
requests/responses; `ctx.prompt` injects a system-prompt section. Full API and
test patterns: **[docs/PLUGIN_GUIDE_FOR_AI.md](docs/PLUGIN_GUIDE_FOR_AI.md)** —
written for AI agents asked to extend this harness.

## Sessions

Every conversation can be saved as `sessions/<name>.json` and switched at any
time. The folder is created automatically on startup.

```
> /sessions                 # numbered list, newest first, * marks the current one
 *1) beta      4 msgs · 2025-06-02 18:41:07 · openrouter/anthropic/claude-sonnet-4.5
        refactor the auth middleware
  2) alpha     9 msgs · 2025-06-02 18:20:12 · groq/llama-3.3-70b-versatile
        explain the build pipeline

> /switch 2                 # by number
> /switch alpha             # by name (alias: /load, /resume)
> /new scratch              # save current, start a fresh session
> /save                     # save to the current name (auto-created if unnamed)
> /save feature-x           # save as a new name
> /delete 2                 # remove a session file
```

- The current session is saved automatically before `/switch`, `/new`, and on
  exit (`auto_save: true`) — you never lose a conversation by switching.
- A session stores messages, system prompt, provider, model and token usage, so
  `/switch` restores the model it was created with (if its key is available).
- Unnamed sessions get a timestamped name such as `session-20250602-184107`.
- Files are plain JSON: copy, edit, diff or commit them as you like.

## API keys (`/keys`)

Keys live in `.env` (git-ignored). `/keys` shows them masked, sets them, and —
the important part — clears them. The CLI starts without any key, so this is
also the first-run setup path; `load_settings()` (the Python API) stays strict
unless you pass `allow_missing_key=True`.

```
> /keys
API keys  ·  C:\...\python ai agent\.env
  set   openrouter   sk-or-…4f2a   (.env) OPENROUTER_API_KEY
  unset opencode-go                OPENCODE_GO_API_KEY, OPENCODE_API_KEY
  unset openai                     OPENAI_API_KEY
  local ollama                     no key needed

> /keys set openrouter sk-or-v1-abc123...
> /keys clear openrouter
> /keys clear all
> /keys path
```

What clearing does:

- removes the assignment(s) from `.env` (comments and other keys are preserved,
  duplicate assignments are collapsed),
- removes the variable from this process's environment immediately,
- removes any `api_key` override for that provider from `config.json`,
- rebuilds the HTTP client, so the active provider fails fast instead of using a
  stale key. If the active provider lost its key you get a warning telling you
  to `/keys set` it again or `/provider <name>` to something else.

Keys inherited from the real environment (not `.env`) can only be cleared for
the current session — the parent shell keeps them. `set` writes only to the
first `api_key_env` listed for the provider. `/keys` never prints a full key.

## MCP servers (`mcp.json`)

The agent is an MCP client: servers configured in the file `mcp_config` points at
(`mcp.json` by default) are started at launch, and every tool they expose becomes
a normal agent tool named `mcp__<server>__<tool>`. To keep everything in one file,
point `"mcp_config": "config.json"` at your main config and put the
`"mcpServers"` block there.

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"},
      "timeout": 60
    },
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
      "env": {},
      "cwd": ".",
      "timeout": 30
    },
    "off-for-now": {
      "command": "uvx",
      "args": ["mcp-server-fetch"],
      "disabled": true
    }
  }
}
```

`${VAR}` is expanded from the environment (which includes `.env`) in `command`,
`args`, `env` values and `cwd`. If an `env` value references an unset variable,
that server is **skipped** with a warning instead of starting with a broken
token — so `mcp.json` can be shared safely with `${GITHUB_TOKEN}` in it.

### GitHub MCP, checked and ready

Add this block to `mcp.json` (a cleaned/shared checkout ships with
`"mcpServers": {}`) to start the official GitHub MCP server. It exposes 26 tools
as `mcp__github__*` (`search_repositories`, `get_file_contents`,
`create_pull_request`, `list_issues`, `push_files`, ...) and was verified end to
end against `github-mcp-server 0.6.2`.

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"},
      "timeout": 60
    }
  }
}
```

```bat
:: one-time: give it a token (same var the models.dev/github provider uses)
setx GITHUB_TOKEN ghp_...          :: or put GITHUB_TOKEN=... in .env
```

- Needs Node.js (for `npx`). First start downloads the package, so allow a few
  seconds; later starts use the npm cache.
- Token scopes: `repo`, `read:org`, `read:user` (or a fine-grained PAT limited to
  the repositories you want the agent to touch). Fine-grained read-only tokens
  work for the read tools.
- No token → the server is skipped with
  `github: environment variable(s) not set: GITHUB_TOKEN ...` and everything else
  keeps working.
- Disable it with `"disabled": true` on the entry (or delete the entry).
- The GitHub tools carry no `readOnlyHint` annotation, so **plan mode** hides and
  blocks them (they can write issues/files/PRs); use build mode for GitHub work.
- Remote MCP endpoints (`https://api.githubcopilot.com/mcp/`) are HTTP, which
  this stdio-only client does not support — the npx server is the stdio route.

```
> /mcp
MCP servers  ·  C:\...\mcp.json
  filesystem         running  5 tools · npx
  github             running  12 tools · npx
  off-for-now        not running  0 tools · uvx
> /mcp tools      # mcp__filesystem__read_file, ...
> /mcp reload     # edit mcp.json while running, then restart
```

| server field | type | required | meaning |
|--------------|------|----------|---------|
| `command` | string | yes | executable to run (`npx`, `uvx`, `python`, absolute path) |
| `args` | array of strings | no | arguments passed to `command` |
| `env` | object | no | extra environment variables for this server only |
| `cwd` | string | no | working directory for the server process |
| `timeout` | number (s) | no (`30`) | request timeout for initialize/list/call |
| `disabled` | bool | no (`false`) | keep the entry documented but do not start it |
| `enabled` | bool | no (`true`) | inverse of `disabled` |
| `type` | string | no | `"stdio"`; `"http"` / `"sse"` are rejected with an error |
| `url` | string | no | rejected — remote transport is unsupported |

Notes:

- `${VAR}` expansion applies to every string field; unset variables in `env`
  skip the server with a warning (missing ones in `args` are left literal).
- **stdio transport only.** `url` / `type: "sse"` / `"http"` entries are rejected
  with a clear configuration error; wrap a remote server in a stdio bridge if
  you need one.
- `env` is merged on top of the agent's environment, so you can pass tokens
  per-server without touching your shell.
- `disabled: true` (or `enabled: false`) keeps an entry documented but unstarted.
- Only **tools** are bridged (`tools/list` + `tools/call`); MCP resources and
  prompts are not surfaced.
- Tools with `annotations.readOnlyHint: true` are marked read-only and stay
  available in plan mode; everything else is hidden and blocked there.
- Tools are namespaced and described with a `[server]` prefix, timeouts apply to
  `initialize` / `tools/list` / `tools/call`, and a server that dies mid-call
  returns an error message to the model instead of crashing the agent.
- On Windows, `npx`/`uvx`/`.cmd` shims are resolved automatically via `cmd.exe`.
- A broken server is reported (`mcp: ...`) and skipped; everything else still
  loads. `/settings mcp_config <path>` switches config files live.
- **Security:** MCP servers are arbitrary local processes. Only add servers you
  trust, and prefer pinning versions (`package@1.2.3`) over `-y latest`.

### Compacting a conversation

Long sessions drift toward the context limit. `/compact` folds everything except
the most recent turns into a summary that lives in the system prompt, so the model
keeps the thread without re-reading the whole transcript.

```
> /compact
compacting conversation…
compacted 42 -> 5 messages · summary 1180 chars · model

> /compact 2        # keep the last 2 user turns verbatim instead of 4
> /compact show     # print the stored summary
```

- **What it keeps:** `compact_keep_turns` (default `4`, configurable in
  `config.json` / `/settings compact_keep_turns`) recent user turns, with whole
  tool exchanges intact. The split always lands on a user message, so a kept
  history never starts with an orphaned tool result.
- **What it folds:** older user/assistant/tool messages, flattened to a
  transcript and summarised by the active model with a fixed prompt that asks for
  goals, constraints, decisions, paths touched, findings, errors, current state
  and pending TODOs.
- **Where the summary goes:** appended to the system prompt under
  `## Earlier conversation (compacted)`; it is not a fake chat message, so tool
  call pairing stays valid.
- **Fallback:** if the summariser call fails (bad key, offline, model refuses),
  a deterministic digest of the folded messages is used instead and you get a
  warning — compaction never blocks.
- **Persistence:** sessions store the summary alongside messages, so
  `/switch`-ing back restores both. `/clear` drops the summary too.
- **Not automatic:** nothing is compacted unless you ask, so tool call history
  stays exact while you need it.

## Command palette

The prompt has a real line editor: typing `/` lists every command name (compact
columns), further letters filter the list and reveal each command's arguments and
description, `Tab` completes, `Up`/`Down` recall history, and
`Left`/`Right`/`Home`/`End`/`Backspace`/`Delete` edit the line. `Ctrl+C`
cancels the line (it does not quit), `Ctrl+D` or `/quit` exits.

```
> /
  /new        /provider   /settings
  /sessions   /providers  /keys
  /switch     /mode       /mcp
  /save       /agents     /usage
  /delete     /tools      /help
  /compact    /skills     /version
  /clear      /plugins    /quit
  /model      /system
  /think      /verbose
  Tab completes · Enter runs · /help for details

> /sw
  /switch <n|name>  switch to another saved session
  Tab completes · Enter runs · /help for details
```

When stdin is not a terminal (pipes, IDE runners) the palette is skipped and
commands keep working normally.

## REPL commands

| command | what it does |
|---------|--------------|
| `/help` | grouped command reference |
| `/new [name]` | save current session, start a fresh one |
| `/sessions` | numbered list with message count, timestamp, model, preview |
| `/switch <n\|name>` | save current, load another (aliases: `/load`, `/resume`) |
| `/save [name]` | save now, optionally under a new name |
| `/delete <n\|name>` | delete the JSON file (current conversation stays in memory) |
| `/compact [turns\|show]` | fold older turns into a summary, keep recent ones verbatim |
| `/clear` | clear the conversation, summary and usage, and wipe the screen (`/cls`) |
| `/model` | fetch the catalogue (validates the key) and open the ↑/↓ picker |
| `/think [level]` | show/set thinking effort (`/reasoning`, `/effort`, `/thinking`) |
| `/model list`, `/model <id>` | print the catalogue, or set a model directly (`/models` alias) |
| `/model info`, `/model refresh` | models.dev details for the current model, or re-fetch lists |
| `/provider <name> [model]` | switch provider (key must exist in `.env`) |
| `/providers` | list configured providers, `*` marks active |
| `/mode [plan\|build]` | show or switch execution mode (prompt shows `plan>`) |
| `/agents [reload]` | show/re-read the loaded AGENTS.md project instructions |
| `/usage` | context %, tokens and estimated cost (`/cost`, `/tokens`, `/context` aliases) |
| `/tools` | list tools, `!` marks dangerous ones |
| `/skills` | list installed skills |
| `/plugins` | list loaded plugins and their tools |
| `/system [text]` | show or replace the system prompt |
| `/verbose` | toggle latency + token stats |
| `/settings [key] [value]` | list/change settings; `save`, `reload`, `reset [key\|all]`, `path` |
| `/keys` | list providers with masked key status |
| `/keys set <provider> <key>` | write a key to `.env`, validate it, load its models |
| `/keys clear <provider\|all>` | erase keys from `.env`, the environment and `config.json` |
| `/keys check [provider\|all]` | validate keys against `/models`, print model counts |
| `/mcp` | list MCP servers, their status and tool counts |
| `/mcp reload`, `/mcp tools`, `/mcp path` | restart servers, list bridge tools, show config path |
| `/version`, `/quit` | version, exit (also `/exit`, `/q`, `Ctrl+D`) |

`Ctrl+C` aborts the current turn and repairs the transcript so the next turn
stays valid.

## CLI flags

```
-p/--prompt TEXT      one-shot prompt, then exit
--mode plan|build     start read-only (plan) or with full access (build)
--clean               wipe keys/sessions/caches + reset configs, then exit
--provider NAME       provider key from the table
-m/--model ID         model id
-w/--workspace DIR    root for relative paths
--config FILE         alternate config.json
--no-stream           wait for the full reply
--yes                 auto-approve dangerous tools
--verbose             latency + token usage
--version
```

## Extending

- **New provider** → `config.json` (`providers`), no code.
- **New tool** → `plugins/*.py` with `ctx.tool(...)`, no core changes.
- **New instructions** → `skills/*/SKILL.md`.
- **Different loop behaviour** (compaction, retries, RAG) → `agent/agent.py`;
  the model client is isolated in `agent/llm.py` and returns a normalised
  `Completion`, so swapping in a native Anthropic/Gemini client touches one file.

## Cleaning up before sharing

Handing the folder to someone else? Remove your keys, history and personal
settings first:

```bat
clean.bat                 :: Windows: shows the plan, asks before touching anything
clean.bat --yes           :: no prompt (scriptable)
```

```sh
./clean.sh                # Linux / macOS / WSL
./clean.sh --yes
./clean.sh --dry-run      # show the plan only
python run.py --clean     # equivalent, works even with no key configured
```

What it does (idempotent — safe to run twice):

| action | target |
|---|---|
| **reset** | `.env` — every `KEY=` blanked, comments and key names kept (template from `.env.example`) |
| **delete** | `.env.bak`, `.env.local`, `.env.save`, `.env.old`, `.env.orig`, `.env.backup`, `.env.production`, `.env.dev` |
| **reset** | `config.json` — clean defaults (workspace `.`, no provider overrides, no custom prompt, mode `build`) |
| **reset** | `mcp.json` — `{"mcpServers": {}}`, so no commands/tokens remain |
| **delete** | `sessions/*.json` — whole conversations (a `.gitkeep` is recreated) |
| **delete** | `.cache/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `*.pyc`, `*.log` |
| **kept** | `agent/`, `tests/`, `skills/`, `plugins/`, `AGENTS.md`, `.env.example`, `run.*`, `clean.*` |

Flags: `-y/--yes`, `--dry-run`, `--keep-config`, `--keep-mcp`
(`python -m agent.cleanup --root <dir>` cleans another copy).

`--clean` is handled **before** settings are loaded, so it works even when the
`.env` is empty, a key is invalid, or `config.json` is broken.

Things cleanup deliberately does **not** decide for you:

- `plugins/` and `skills/` are your own code — check them for hardcoded tokens,
  internal URLs or sample data before sharing.
- `AGENTS.md` is project documentation; review it (it may mention private systems).
- Anything you copied in by hand: `.venv/`, logs elsewhere, editor state, a
  `config.json` you renamed, test fixtures with real data.

Afterwards the harness is back to a fresh state: set keys with `/keys set
<provider> <key>` or by editing the blank `.env`, and everything else is
recreated on demand.

## Publishing to GitHub

The folder is a self-contained repo: no dependencies, everything runs from
source, and local secrets/state are git-ignored.

```bat
:: Windows
clean.bat --keep-config --keep-mcp --yes
```
```sh
# Linux / macOS / WSL
./clean.sh --keep-config --keep-mcp --yes
```

`--keep-config --keep-mcp` keeps the shipped templates (`config.json`,
`mcp.json` with `${GITHUB_TOKEN}`); a full clean resets them to empty defaults.
Either way `.env`, `sessions/` and `.cache/` are never committed.

```bash
git init -b main
git add .
git status                  # verify: no .env, no .cache, no sessions/*.json
git commit -m "Initial commit: Python AI agent harness"
git remote add origin https://github.com/<you>/python-ai-agent.git
git push -u origin main
```

Checklist (all verified for this tree):

- [x] `.env` / `.env.*` ignored, only `.env.example` tracked
- [x] `sessions/*.json` and `.cache/` ignored
- [x] `config.json` and `mcp.json` contain no keys (MCP uses `${GITHUB_TOKEN}`)
- [x] no `ghp_…` / `sk-…` / private-key strings anywhere in the tree
- [x] no absolute personal paths in code or docs
- [x] `.gitattributes` keeps `.bat` CRLF and everything else LF
- [x] `LICENSE` (MIT — replace `<Your Name>`), `SECURITY.md`
- [x] CI in `.github/workflows/tests.yml`: offline smoke tests on Linux, Windows
      and macOS (Python 3.10 + 3.12) plus the pty palette/picker test on Ubuntu
- [x] `python tests/smoke_test.py` runs with no API key and no network
      (`AGENT_OFFLINE=1` is set by the test itself)

Before you push, run `git grep -nE "ghp_|sk-|github_pat_|BEGIN .*PRIVATE KEY"`
and skim `plugins/`, `skills/`, `AGENTS.md` — those are yours, and the cleanup
script deliberately leaves them untouched.

Suggested repository settings: description + topics (`ai-agent`, `llm`,
`mcp`, `python`, `openrouter`), default branch `main`, and enable
"Report a vulnerability" (used by `SECURITY.md`).

## Troubleshooting

- `Model error: No API key for provider ...` → the harness started fine; set the
  key with `/keys set <provider> <key>` (or in `.env`), or `/provider ollama`
  / `lmstudio` for local models.
- `Configuration error: ...` at launch → something other than a missing key
  (unknown provider, invalid `mode`/`shell`, broken JSON). Fix `config.json`.
- `HTTP 401` → key wrong for that provider (OpenRouter keys don't work on Groq).
- `HTTP 403` on `/models` → provider/WAF refused the model list; the key is not
  necessarily invalid. The harness sends a real User-Agent and falls back to the
  models.dev catalogue; try `/model list` again or `/model <id>`.
- `HTTP 404 model not found` → model id must match the provider's catalogue
  (e.g. `anthropic/claude-sonnet-4.5` on OpenRouter vs `claude-sonnet-4-5` on
  Anthropic/OpenCode).
- Tools never get called → some local models need a bigger context or don't
  support tool calling; try `--no-stream` or a different model.
- Windows console shows escape codes → run in Windows Terminal; ANSI is enabled
  automatically where possible.
