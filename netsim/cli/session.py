"""CLI command tree and session."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from ..net.device import Device
from ..net.world import World


@dataclass
class Cmd:
    name: str
    aliases: List[str] = field(default_factory=list)
    help: str = ""
    children: List["Cmd"] = field(default_factory=list)
    handler: Optional[Callable[["Session", List[str]], None]] = None
    min_args: int = 0
    max_args: int = 0

    def add(self, child: "Cmd") -> "Cmd":
        self.children.append(child)
        return self

    def find(self, token: str) -> Optional["Cmd"]:
        token = token.lower()
        for ch in self.children:
            if ch.name.lower() == token:
                return ch
        match = None
        ambiguous = False
        for ch in self.children:
            if token and ch.name.lower().startswith(token):
                if match is not None:
                    ambiguous = True
                    continue
                match = ch
        if ambiguous:
            return None
        return match

    def tab_expand(self, token: str) -> str:
        token = token.lower()
        matches = [ch.name for ch in self.children if ch.name.lower().startswith(token)]
        if len(matches) == 1:
            return matches[0]
        return ""

    def help_text(self) -> List[str]:
        out = []
        for ch in self.children:
            if ch.help:
                out.append(f"  {ch.name}  {ch.help}")
            else:
                out.append(f"  {ch.name}")
        return out


@dataclass
class View:
    id: str
    prompt: Callable[["Session"], str]
    tree: Cmd


class Session:
    def __init__(self, device_id: str, device_name: str, vendor):
        self.device_id = device_id
        self.device_name = device_name
        self.vendor = vendor
        self.views: List[View] = []
        self.dev: Optional[Device] = None
        self.world: Optional[World] = None
        self.output: List[str] = []
        self.on_output = None  # callable(text) for streaming async output

    # ---- view stack ----

    def push(self, view: View):
        self.views.append(view)

    def pop(self) -> Optional[View]:
        if len(self.views) <= 1:
            return self.views[0]
        self.views.pop()
        return self.current()

    def return_view(self) -> View:
        if len(self.views) > 1:
            self.views = self.views[:1]
        return self.views[0]

    def current(self) -> View:
        return self.views[-1]

    def prompt_str(self) -> str:
        return self.current().prompt(self)

    # ---- output ----

    def p(self, s: str = ""):
        self.output.append(s)

    def pformat(self, fmt: str, *args):
        self.output.append(fmt % args if args else fmt)

    def flush(self) -> str:
        text = "\n".join(self.output)
        self.output = []
        return text

    def show(self, text: str):
        self.output.append(text.rstrip("\n"))

    def stream(self, text: str):
        """Append output and push it to the client immediately (for async replies)."""
        self.output.append(text)
        if self.on_output is not None:
            self.on_output("\n".join(self.output))
            self.output = []

    # ---- execution ----

    def execute(self, line: str) -> str:
        self.output = []
        fields = line.split()
        if not fields:
            return ""
        tree = self.current().tree
        if fields[0] == "?":
            for l in tree.help_text():
                self.p(l)
            return self.flush()
        self._run(tree, fields)
        return self.flush()

    def run_as(self, tree: Cmd, line: str):
        fields = line.split()
        if fields:
            self._run(tree, fields)

    def _run(self, tree: Cmd, args: List[str]):
        first = args[0]
        if first == "?":
            for l in tree.help_text():
                self.p(l)
            return
        node = tree.find(first)
        if node is None:
            self.p("  Unknown command, or the command does not have the specified parameters.")
            return
        if node.children:
            if len(args) == 1:
                self.p("  Incomplete command.")
                return
            self._run(node, args[1:])
            return
        if node.handler is None:
            self.p("  Incomplete command.")
            return
        if len(args) - 1 < node.min_args:
            self.p("  Incomplete command.")
            return
        node.handler(self, args[1:])

    def tab_expand_line(self, line: str) -> str:
        fields = line.split()
        if not fields:
            return line
        tree = self.current().tree
        i = 0
        while i < len(fields) - 1:
            node = tree.find(fields[i])
            if node is None or not node.children:
                break
            tree = node
            i += 1
        last = fields[-1]
        exp = tree.tab_expand(last)
        if not exp:
            return line
        fields[-1] = exp
        return " ".join(fields)


class Vendor:
    def name(self) -> str:
        raise NotImplementedError

    def version_info(self) -> str:
        raise NotImplementedError

    def new_session(self, device_id: str, device_name: str) -> Session:
        raise NotImplementedError