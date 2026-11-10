"""Tool registry: JSON schemas handed to the model, Python callables behind them."""

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

ToolFunc = Callable[..., Any]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    func: ToolFunc
    dangerous: bool = False
    read_only: bool = False
    priority: int = 0

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
                or {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }


@dataclass
class Registry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any] | None = None,
        dangerous: bool = False,
        read_only: bool = False,
        priority: int = 0,
    ) -> Callable[[ToolFunc], ToolFunc]:
        """Decorator: `@registry.register("name", "what it does", schema)`."""

        def decorator(func: ToolFunc) -> ToolFunc:
            self.add(
                name, description, parameters, func,
                dangerous=dangerous, read_only=read_only, priority=priority,
            )
            return func

        return decorator

    def add(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any] | None,
        func: ToolFunc,
        dangerous: bool = False,
        read_only: bool = False,
        priority: int = 0,
    ) -> Tool:
        if not name or not callable(func):
            raise ValueError("a tool needs a name and a callable")
        if name in self.tools:
            raise ValueError(f"duplicate tool name: {name}")
        tool = Tool(
            name=name,
            description=description,
            parameters=parameters or {"type": "object", "properties": {}},
            func=func,
            dangerous=dangerous,
            read_only=read_only,
            priority=priority,
        )
        self.tools[name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def names(self) -> list[str]:
        return sorted(self.tools)

    def specs(self, only: set[str] | None = None) -> list[dict[str, Any]]:
        names = list(self.tools) if only is None else [n for n in self.tools if n in only]
        # High-priority tools are listed first in the schema, which biases models
        # toward them; run_python outranks run_command by design.
        names.sort(key=lambda name: (-self.tools[name].priority, name))
        return [self.tools[name].spec() for name in names]

    def read_only_names(self) -> set[str]:
        return {name for name, tool in self.tools.items() if tool.read_only}

    def describe(self) -> list[dict[str, str]]:
        return [
            {
                "name": tool.name,
                "description": tool.description.strip().splitlines()[0]
                if tool.description
                else "",
                "dangerous": "yes" if tool.dangerous else "no",
                "read_only": "yes" if tool.read_only else "no",
            }
            for tool in (self.tools[name] for name in sorted(self.tools))
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Run a tool. Returns (text result, is_error)."""
        tool = self.tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'. Available: {', '.join(self.names())}", True
        try:
            result = tool.func(**arguments)
        except TypeError as exc:
            return f"Error calling '{name}': bad arguments ({exc})", True
        except Exception as exc:  # noqa: BLE001 - tool failures are data, not crashes
            detail = f"{type(exc).__name__}: {exc}"
            return f"Error in '{name}': {detail}", True
        return render_result(result), False


def render_result(result: Any) -> str:
    if result is None:
        return "ok"
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(result)


def format_exception(exc: BaseException) -> str:
    return "".join(traceback.format_exception_only(type(exc), exc)).strip()
