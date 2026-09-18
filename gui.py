"""Optional tkinter front end for the Shenzhen Metro Smart Navigator.

Same engine as the CLI, different skin: :mod:`navigator` is imported, never
reimplemented. The interesting widget is :class:`StationPicker`, a pair of
dependent combo boxes -- choosing a line refills the station box with only
that line's stations, which is what makes typos impossible.

Run it with ``python3 main.py --gui`` on an interpreter that ships tkinter.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from navigator import (
    InstructionGenerator,
    MetroError,
    MetroNetwork,
    Route,
    Router,
)

BACKGROUND = "#11161d"
SURFACE = "#1b2430"
FOREGROUND = "#e8edf3"
MUTED = "#8b9bb0"
ACCENT = "#00a651"

TITLE_FONT = ("Helvetica", 20, "bold")
LABEL_FONT = ("Helvetica", 11)
BODY_FONT = ("Helvetica", 13)
MONO_FONT = ("Menlo", 12)


class StationPicker(ttk.Frame):
    """Two combo boxes where the second depends on the first."""

    def __init__(
        self,
        master: tk.Misc,
        network: MetroNetwork,
        role: str,
        lang: str,
        on_change: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(master, padding=(0, 6))
        self.network = network
        self.lang = lang
        self.on_change = on_change
        self._station_ids: list[str] = []

        ttk.Label(self, text=role, style="Role.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 4), columnspan=2
        )

        self.line_var = tk.StringVar()
        self.line_box = ttk.Combobox(
            self, textvariable=self.line_var, state="readonly", width=18
        )
        self.line_box["values"] = [
            self.network.lines[name].label(lang) for name in network.line_names()
        ]
        self.line_box.grid(row=1, column=0, sticky="ew", padx=(0, 10))
        self.line_box.bind("<<ComboboxSelected>>", self._on_line_selected)

        self.station_var = tk.StringVar()
        self.station_box = ttk.Combobox(
            self, textvariable=self.station_var, state="disabled", width=34
        )
        self.station_box.grid(row=1, column=1, sticky="ew")
        self.station_box.bind("<<ComboboxSelected>>", lambda _e: self._notify())

        self.columnconfigure(1, weight=1)

    # -- behaviour ---------------------------------------------------------

    def _on_line_selected(self, _event: tk.Event) -> None:
        """Refill the station box with only the chosen line's stations."""
        index = self.line_box.current()
        if index < 0:
            return
        line_name = self.network.line_names()[index]
        self._station_ids = list(self.network.lines[line_name].stations)
        self.station_box["values"] = [
            self._label(sid) for sid in self._station_ids
        ]
        self.station_box.state(["!disabled", "readonly"])
        self.station_var.set("")
        self._notify()

    def _label(self, station_id: str) -> str:
        station = self.network.station(station_id)
        mark = "  ⇄" if station.is_transfer else ""
        return f"{station.name(self.lang)}{mark}"

    def _notify(self) -> None:
        if self.on_change:
            self.on_change()

    # -- state -------------------------------------------------------------

    @property
    def station_id(self) -> Optional[str]:
        index = self.station_box.current()
        if index < 0 or index >= len(self._station_ids):
            return None
        return self._station_ids[index]

    def set_station(self, station_id: str) -> None:
        """Point both boxes at a station -- used by the Swap button."""
        line_name = self.network.station(station_id).lines[0]
        self.line_box.current(self.network.line_names().index(line_name))
        self._station_ids = list(self.network.lines[line_name].stations)
        self.station_box["values"] = [self._label(s) for s in self._station_ids]
        self.station_box.state(["!disabled", "readonly"])
        self.station_box.current(self._station_ids.index(station_id))

    def clear(self) -> None:
        self.line_var.set("")
        self.station_var.set("")
        self._station_ids = []
        self.station_box["values"] = []
        self.station_box.state(["disabled"])


class NavigatorApp(tk.Tk):
    """The main window."""

    def __init__(self, network: MetroNetwork, lang: str = "both") -> None:
        super().__init__()
        self.network = network
        self.router = Router(network)
        self.lang = lang

        self.title("Shenzhen Metro Smart Navigator")
        self.configure(background=BACKGROUND)
        self.minsize(760, 620)
        self._build_styles()
        self._build_widgets()

    # -- chrome ------------------------------------------------------------

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BACKGROUND)
        style.configure("Card.TFrame", background=SURFACE)
        style.configure(
            "TLabel", background=BACKGROUND, foreground=FOREGROUND, font=LABEL_FONT
        )
        style.configure("Role.TLabel", foreground=MUTED, font=("Helvetica", 10, "bold"))
        style.configure("Title.TLabel", font=TITLE_FONT, foreground=FOREGROUND)
        style.configure("Sub.TLabel", foreground=MUTED, font=("Helvetica", 10))
        style.configure("TButton", font=LABEL_FONT, padding=(14, 8))
        style.configure(
            "Go.TButton", font=("Helvetica", 12, "bold"), padding=(18, 10)
        )
        style.configure(
            "TRadiobutton", background=BACKGROUND, foreground=FOREGROUND,
            font=("Helvetica", 10),
        )
        style.map("TRadiobutton", background=[("active", BACKGROUND)])

    def _build_widgets(self) -> None:
        root = ttk.Frame(self, padding=22)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(4, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="🚇  Shenzhen Metro", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text=(
                f"{len(self.network.lines)} lines · "
                f"{len(self.network.stations)} stations · "
                f"{len(self.network.transfer_stations())} transfer points · offline"
            ),
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        pickers = ttk.Frame(root)
        pickers.grid(row=1, column=0, sticky="ew", pady=(18, 6))
        pickers.columnconfigure(0, weight=1)

        self.origin = StationPicker(
            pickers, self.network, "FROM  出发", self.lang, self._refresh_button
        )
        self.origin.grid(row=0, column=0, sticky="ew")
        self.destination = StationPicker(
            pickers, self.network, "TO  到达", self.lang, self._refresh_button
        )
        self.destination.grid(row=1, column=0, sticky="ew")

        options = ttk.Frame(root)
        options.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.optimize_var = tk.StringVar(value="time")
        ttk.Label(options, text="Optimise for:", style="Sub.TLabel").pack(side="left")
        for value, text in (("time", "fastest"), ("stops", "fewest stops")):
            ttk.Radiobutton(
                options, text=text, value=value, variable=self.optimize_var
            ).pack(side="left", padx=(10, 0))

        buttons = ttk.Frame(root)
        buttons.grid(row=3, column=0, sticky="ew", pady=(14, 10))
        self.go_button = ttk.Button(
            buttons, text="Find route", style="Go.TButton",
            command=self.find_route, state="disabled",
        )
        self.go_button.pack(side="left")
        ttk.Button(buttons, text="⇅ Swap", command=self.swap).pack(side="left", padx=8)
        ttk.Button(buttons, text="Clear", command=self.clear).pack(side="left")

        self.output = tk.Text(
            root, wrap="word", font=MONO_FONT, background=SURFACE,
            foreground=FOREGROUND, insertbackground=FOREGROUND,
            relief="flat", padx=16, pady=16, height=16,
        )
        self.output.grid(row=4, column=0, sticky="nsew")
        self.output.configure(state="disabled")

        self.output.tag_configure("head", font=("Helvetica", 14, "bold"))
        self.output.tag_configure("muted", foreground=MUTED)
        self.output.tag_configure("total", font=("Helvetica", 12, "bold"),
                                  foreground=ACCENT)
        for name, line in self.network.lines.items():
            self.output.tag_configure(f"line:{name}", foreground=line.color)

        self._write_placeholder()

    # -- actions -----------------------------------------------------------

    def _refresh_button(self) -> None:
        ready = self.origin.station_id and self.destination.station_id
        self.go_button.state(["!disabled"] if ready else ["disabled"])

    def swap(self) -> None:
        start, end = self.origin.station_id, self.destination.station_id
        if start and end:
            self.origin.set_station(end)
            self.destination.set_station(start)
            self._refresh_button()

    def clear(self) -> None:
        self.origin.clear()
        self.destination.clear()
        self._refresh_button()
        self._write_placeholder()

    def find_route(self) -> None:
        start, end = self.origin.station_id, self.destination.station_id
        if not (start and end):
            return
        try:
            route = self.router.find_route(
                start, end, optimize=self.optimize_var.get()
            )
        except MetroError as exc:
            self._render_error(str(exc))
            return
        self._render_route(route)

    # -- output ------------------------------------------------------------

    def _clear_output(self) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")

    def _seal_output(self) -> None:
        self.output.configure(state="disabled")

    def _write_placeholder(self) -> None:
        self._clear_output()
        self.output.insert(
            "end",
            "Pick a line, then a station, for both ends of your trip.\n"
            "⇄ marks a transfer station.\n",
            "muted",
        )
        self._seal_output()

    def _render_error(self, message: str) -> None:
        self._clear_output()
        self.output.insert("end", f"⚠  {message}\n", "muted")
        self._seal_output()

    @staticmethod
    def _line_of(step: dict) -> Optional[str]:
        return step.get("to_line") or step.get("line")

    def _render_route(self, route: Route) -> None:
        voice = InstructionGenerator(self.network, self.lang)
        self._clear_output()

        if route.is_trivial:
            self.output.insert("end", voice.render(route) + "\n", "head")
            self._seal_output()
            return

        origin = self.network.station(route.start).name(self.lang)
        destination = self.network.station(route.end).name(self.lang)
        self.output.insert("end", f"{origin}  →  {destination}\n\n", "head")

        for number, (step, sentence) in enumerate(
            zip(route.steps, voice.steps(route)), start=1
        ):
            line_name = self._line_of(step)
            tag = f"line:{line_name}" if line_name in self.network.lines else "muted"
            self.output.insert("end", "  ■  ", tag)
            self.output.insert("end", f"{number}. {sentence}\n")

        self.output.insert("end", "\n" + voice.summary(route) + "\n", "total")
        self.output.insert(
            "end", "Lines: " + "  →  ".join(route.lines_used) + "\n", "muted"
        )
        self._seal_output()


def run_gui(network: MetroNetwork, lang: str = "both") -> int:
    """Start the tkinter event loop. Returns a process exit code."""
    NavigatorApp(network, lang=lang).mainloop()
    return 0
