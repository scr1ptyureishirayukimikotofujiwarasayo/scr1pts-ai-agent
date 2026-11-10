"""Example plugin: a couple of tools plus a hook.

Copy this file as a starting point for your own plugins.
"""

from __future__ import annotations

import datetime as _dt

NAME = "clock"
DESCRIPTION = "Time/date tools and a hook that logs slow turns"


def register(ctx):
    @ctx.tool(
        "now",
        "Current local date and time (optionally in another IANA timezone, e.g. 'Europe/Berlin').",
        {
            "type": "object",
            "properties": {"timezone": {"type": "string", "default": "local"}},
        },
    )
    def now(timezone: str = "local") -> str:
        stamp = _dt.datetime.now().astimezone()
        if timezone and timezone != "local":
            try:
                from zoneinfo import ZoneInfo

                stamp = _dt.datetime.now(ZoneInfo(timezone))
            except Exception as exc:  # noqa: BLE001 - report, do not crash
                return f"Error: unknown timezone '{timezone}' ({exc})"
        return stamp.strftime("%Y-%m-%d %H:%M:%S %Z (%A)")

    # Hooks are observation points. Returning a string from on_user_message
    # replaces the user's message before it is sent to the model.
    def on_turn_end(text):
        if not text:
            ctx.log("[clock] model returned an empty answer")

    ctx.hook("on_turn_end", on_turn_end)
