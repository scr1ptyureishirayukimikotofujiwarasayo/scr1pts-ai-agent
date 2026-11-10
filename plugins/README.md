# Plugins

This folder is empty on purpose — a fresh checkout reports `plugins: 0`.

Drop a `.py` file here to add tools or hooks at startup; it is imported on every
launch. A broken plugin is reported and skipped.

See [`examples/plugins/clock.py`](../examples/plugins/clock.py) for a working
template (a `now` tool plus an `on_turn_end` hook). Copy it here to enable it:

```bat
copy examples\plugins\clock.py plugins\clock.py
```
```sh
cp examples/plugins/clock.py plugins/clock.py
```

The plugin API is documented in the main README under "Plugins".
