"""
Native Tkinter Desktop GUI for Upstox 5-Minute F&O Intraday Reversal Scanner.
Sleek Modern Dark Dashboard with Real-Time Pivots, Narrow CPR, and Trap Zones.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone, timedelta
import logging
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from typing import Any, Dict, List, Optional, Set

try:
    import winsound
except ImportError:
    winsound = None

import config
from web.state import dashboard_state
from gui.chart import CandleChartFrame

logger = logging.getLogger(__name__)
IST_TZ = timezone(timedelta(hours=5, minutes=30))

# Modern Dark Theme Color Palette
BG_DARK = "#0f172a"        # Deep slate background
CARD_BG = "#1e293b"        # Slate card background
CARD_BORDER = "#334155"    # Subtle borders
HEADER_BG = "#0f172a"      # Header slate
ACCENT_BLUE = "#38bdf8"    # Cyan / Sky Blue
ACCENT_GREEN = "#10b981"   # Emerald Green (Bullish)
ACCENT_RED = "#f43f5e"     # Rose Red (Bearish)
ACCENT_AMBER = "#f59e0b"   # Amber (Warning / Narrow CPR)
TEXT_MAIN = "#f8fafc"      # Bright White
TEXT_MUTED = "#94a3b8"     # Secondary Gray
TREE_BG = "#0d1527"        # Dark table background
TREE_ALT = "#131f38"       # Alternating row background
TREE_SEL = "#1e40af"       # Selected row


class MultiSelectDropdown(tk.Menubutton):
    """
    A modern dark-themed multi-select dropdown widget with interactive checkboxes.
    """
    def __init__(self, master, title: str, options: List[str], on_change_callback: Optional[Callable] = None, default_selected: Optional[Iterable[str]] = None, **kwargs):
        super().__init__(
            master,
            text=f"{title}: ALL ▾",
            relief="flat",
            bg=CARD_BG,
            fg=TEXT_MAIN,
            activebackground=CARD_BORDER,
            activeforeground=ACCENT_BLUE,
            font=("Segoe UI", 9, "bold"),
            highlightthickness=1,
            highlightbackground=CARD_BORDER,
            padx=10,
            pady=3,
            cursor="hand2",
            **kwargs
        )
        self.title = title
        self.options = options
        self.on_change_callback = on_change_callback
        self.menu = tk.Menu(
            self,
            tearoff=False,
            bg=CARD_BG,
            fg=TEXT_MAIN,
            activebackground=TREE_SEL,
            activeforeground=TEXT_MAIN,
            font=("Segoe UI", 9)
        )
        self["menu"] = self.menu

        default_set = set(default_selected) if default_selected is not None else None
        is_all_default = default_set is None or len(default_set) == len(options)
        self.all_var = tk.BooleanVar(value=is_all_default)
        self.vars: Dict[str, tk.BooleanVar] = {}

        # "Select ALL" checkbutton
        self.menu.add_checkbutton(label="✓ (Select ALL)", variable=self.all_var, command=self._on_toggle_all)
        self.menu.add_separator()

        for opt in options:
            init_val = True if default_set is None else (opt in default_set)
            var = tk.BooleanVar(value=init_val)
            self.vars[opt] = var
            self.menu.add_checkbutton(label=opt, variable=var, command=self._on_toggle_option)

        self._update_button_text()

    def _on_toggle_all(self):
        val = self.all_var.get()
        for var in self.vars.values():
            var.set(val)
        self._update_button_text()
        if self.on_change_callback:
            self.on_change_callback()

    def _on_toggle_option(self):
        all_checked = all(var.get() for var in self.vars.values())
        self.all_var.set(all_checked)
        self._update_button_text()
        if self.on_change_callback:
            self.on_change_callback()

    def _update_button_text(self):
        selected = [opt for opt, var in self.vars.items() if var.get()]
        if len(selected) == len(self.options) or len(selected) == 0:
            self.config(text=f"{self.title}: ALL ▾")
        elif len(selected) == 1:
            short_opt = selected[0].split("(")[0].strip()
            self.config(text=f"{self.title}: {short_opt} ▾")
        else:
            self.config(text=f"{self.title}: {len(selected)} Selected ▾")

    def get_selected(self) -> Set[str]:
        """Returns the set of currently checked option strings."""
        return {opt for opt, var in self.vars.items() if var.get()}

    def is_all_selected(self) -> bool:
        sel = self.get_selected()
        return len(sel) == len(self.options) or len(sel) == 0


class ScannerTkinterGUI:
    """High-performance Tkinter Desktop GUI for Upstox F&O Intraday Scanner."""

    def __init__(self, root: tk.Tk, scanner=None):
        self.root = root
        self.scanner = scanner
        self.db_repo = getattr(scanner, "db", None) if scanner else None
        if not self.db_repo:
            try:
                from database.repository import DatabaseRepository
                self.db_repo = DatabaseRepository()
            except Exception:
                self.db_repo = None
        self.chart_symbols_loaded = False

        self.root.title("Upstox 5-Minute F&O Intraday Reversal Scanner - Live Desktop Dashboard")
        self.root.geometry("1480x880")
        self.root.minsize(1100, 700)
        self.root.configure(bg=BG_DARK)

        # Application State Tracking
        self.is_audio_enabled = True
        self.last_signal_count = 0
        self.last_market_hash = ""
        self.last_price_version = -1
        self.cached_signals: List[dict] = []
        self.cached_market: List[dict] = []
        self.last_market_render_time = 0.0
        self.last_chart_render_time = 0.0
        self.market_dirty = False
        self.chart_dirty = False

        # Filters & Sorting
        self.signal_direction_var = tk.StringVar(value="ALL")
        self.signal_tf_var = tk.StringVar(value="ALL")
        self.signal_pattern_var = tk.StringVar(value="ALL")
        self.signal_status_var = tk.StringVar(value="ALL STATUS")
        self.signal_vol_var = tk.StringVar(value="ALL VOLUMES")
        self.signal_score_var = tk.StringVar(value="🔥 High Conviction (>= 7)")
        self.signal_liq_var = tk.StringVar(value="ALL LIQUIDITY")
        self.strict_zones_var = tk.BooleanVar(value=True)
        self.signal_cpr_var = tk.StringVar(value="ALL")
        self.signal_search_var = tk.StringVar(value="")
        self.signal_sort_var = tk.StringVar(value="⏱️ Time (Newest First)")
        self.signals_sort_col = "time"
        self.signals_sort_rev = True

        self.market_cpr_var = tk.StringVar(value="ALL")
        self.market_liq_var = tk.StringVar(value="ALL LIQUIDITY")
        self.market_search_var = tk.StringVar(value="")
        self.market_sort_var = tk.StringVar(value="⚡ CPR % (Narrowest First)")
        self.market_sort_col = "cpr_pct"
        self.market_sort_rev = False

        # HEMA + T3 Strategy Scanner Filters & State
        self.cached_hema_signals: List[dict] = []
        self.hema_tf_var = tk.StringVar(value="ALL")
        self.hema_signal_var = tk.StringVar(value="ALL")
        self.hema_regime_var = tk.StringVar(value="ALL")
        self.hema_score_var = tk.StringVar(value="ALL")
        self.hema_sideways_filter_var = tk.StringVar(value="ALL")
        self.hema_search_var = tk.StringVar(value="")
        self.hema_sort_var = tk.StringVar(value="⏱️ Time (Newest First)")
        self.hema_dirty = False
        self.last_hema_render_time = 0.0

        # Chartink Intraday Screener Filters & State
        self.cached_chartink_signals: List[dict] = []
        self.chartink_strategy_var = tk.StringVar(value="ALL STRATEGIES")
        self.chartink_search_var = tk.StringVar(value="")
        self.chartink_sort_var = tk.StringVar(value="⏱️ Time (Newest First)")
        self.chartink_dirty = False
        self.last_chartink_render_time = 0.0

        # Setup Styling & UI Components
        self._setup_styles()
        self._build_header()
        self._build_metric_cards()
        self._build_notebook()
        self._build_footer()

        # Start Real-Time Update Loop
        self._poll_data()
        self._update_clock()

        # Background recurring auto-scan loops (hands-free continuous scanning)
        self._schedule_auto_hema_scan()
        self.root.after(1000, self._schedule_auto_chartink_scan)

    def _debounce(self, key: str, delay_ms: int, callback):
        """Cancels any pending callback for the given key and schedules a new one."""
        if not hasattr(self, "_debounce_timers"):
            self._debounce_timers = {}
        timer = self._debounce_timers.get(key)
        if timer:
            try:
                self.root.after_cancel(timer)
            except Exception:
                pass
        self._debounce_timers[key] = self.root.after(delay_ms, callback)

    def _schedule_auto_hema_scan(self):
        """Periodically scans HEMA + T3 across universe in background without requiring manual clicks."""
        try:
            has_univ = hasattr(self.scanner, "_universe") and bool(self.scanner._universe)
            is_running = getattr(self.scanner, "_is_running", False)
            if hasattr(self, "scanner") and self.scanner and (has_univ or is_running):
                if not getattr(self.scanner, "_is_hema_scanning", False):
                    if not self.cached_hema_signals or is_running:
                        self._trigger_hema_scan(is_auto=True)
        except Exception:
            pass
        delay = 3000 if not self.cached_hema_signals else 60000
        self.root.after(delay, self._schedule_auto_hema_scan)

    def _schedule_auto_chartink_scan(self):
        """Continuously re-evaluates Chartink screener formulas in real-time background loop every 10 seconds."""
        try:
            if hasattr(self, "scanner") and self.scanner:
                if not getattr(self.scanner, "_is_chartink_scanning", False):
                    self._trigger_chartink_scan(is_auto=True)
        except Exception:
            pass
        # Auto-evaluate every 10 seconds continuously (hands-free real-time operation)
        self.root.after(10000, self._schedule_auto_chartink_scan)

    def _setup_styles(self):
        """Configures modern dark ttk styles for notebook, treeviews, and inputs."""
        self.style = ttk.Style(self.root)
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        # Configure General TTK Widgets
        self.style.configure(".", background=BG_DARK, foreground=TEXT_MAIN, font=("Segoe UI", 9))

        # Notebook Tabs
        self.style.configure("TNotebook", background=BG_DARK, borderwidth=0)
        self.style.configure("TNotebook.Tab", background=CARD_BG, foreground=TEXT_MUTED, padding=[16, 8], font=("Segoe UI", 10, "bold"))
        self.style.map("TNotebook.Tab",
            background=[("selected", ACCENT_BLUE)],
            foreground=[("selected", "#000000")],
        )

        # Treeview Styling
        self.style.configure("Treeview",
            background=TREE_BG,
            foreground=TEXT_MAIN,
            fieldbackground=TREE_BG,
            rowheight=26,
            font=("Segoe UI", 9),
            borderwidth=0,
        )
        self.style.configure("Treeview.Heading",
            background=CARD_BG,
            foreground=ACCENT_BLUE,
            font=("Segoe UI", 9, "bold"),
            borderwidth=1,
            relief="flat",
        )
        self.style.map("Treeview.Heading",
            background=[("active", CARD_BORDER)],
            foreground=[("active", TEXT_MAIN)],
        )
        self.style.map("Treeview",
            background=[("selected", TREE_SEL)],
            foreground=[("selected", "#ffffff")],
        )

        # Combobox & Entry
        self.style.configure("TCombobox", fieldbackground=CARD_BG, background=CARD_BG, foreground=TEXT_MAIN)
        self.style.configure("TEntry", fieldbackground=CARD_BG, foreground=TEXT_MAIN)

    def _build_header(self):
        """Builds top header bar with title and quick controls."""
        header_frame = tk.Frame(self.root, bg=BG_DARK, pady=10, padx=16)
        header_frame.pack(fill=tk.X)

        # Title & Subtitle
        title_box = tk.Frame(header_frame, bg=BG_DARK)
        title_box.pack(side=tk.LEFT)

        title_lbl = tk.Label(
            title_box,
            text="⚡ UPSTOX 5M F&O REVERSAL SCANNER",
            font=("Segoe UI", 14, "bold"),
            fg=TEXT_MAIN,
            bg=BG_DARK,
        )
        title_lbl.pack(anchor="w")

        sub_lbl = tk.Label(
            title_box,
            text="Real-Time Candlestick Reversals • Daily Pivots (PP, R1-R3, S1-S3, CPR) • Trap Zones (R1-PDH, S1-PDL)",
            font=("Segoe UI", 9),
            fg=TEXT_MUTED,
            bg=BG_DARK,
        )
        sub_lbl.pack(anchor="w")

        # Top Right Controls (Market Mode, Audio & Clock)
        ctrl_box = tk.Frame(header_frame, bg=BG_DARK)
        ctrl_box.pack(side=tk.RIGHT)

        # Universe Selector: F&O (210 Option Stocks), Nifty 250, Nifty 500
        tk.Label(
            ctrl_box,
            text="Universe:",
            font=("Segoe UI", 9, "bold"),
            fg=TEXT_MUTED,
            bg=BG_DARK,
        ).pack(side=tk.LEFT, padx=(0, 4))

        init_univ = getattr(self.scanner, "universe_name", getattr(config, "DEFAULT_UNIVERSE", "NIFTY500")) if self.scanner else getattr(config, "DEFAULT_UNIVERSE", "NIFTY500")
        univ_display_map = {
            "NIFTY500": "🌐 Nifty 500 (Broad Market)",
            "NIFTY250": "🏛️ Nifty 250 (LargeMidcap)",
            "FNO": "🔥 F&O Option Stocks (210)",
        }
        self.universe_var = tk.StringVar(value=univ_display_map.get(init_univ.upper(), "🌐 Nifty 500 (Broad Market)"))
        self.universe_combo = ttk.Combobox(
            ctrl_box,
            textvariable=self.universe_var,
            values=[
                "🌐 Nifty 500 (Broad Market)",
                "🏛️ Nifty 250 (LargeMidcap)",
                "🔥 F&O Option Stocks (210)",
            ],
            state="readonly",
            width=25,
            font=("Segoe UI", 9, "bold"),
        )
        self.universe_combo.pack(side=tk.LEFT, padx=(0, 10))
        self.universe_combo.bind("<<ComboboxSelected>>", self._on_universe_changed)

        # Mode Indicator: Fixed to Cash Equity
        tk.Label(
            ctrl_box,
            text="Mode:",
            font=("Segoe UI", 9, "bold"),
            fg=TEXT_MUTED,
            bg=BG_DARK,
        ).pack(side=tk.LEFT, padx=(0, 4))

        self.market_mode_var = tk.StringVar(value="SPOT")
        mode_badge = tk.Label(
            ctrl_box,
            text="📈 EQUITY (Cash)",
            font=("Segoe UI", 9, "bold"),
            fg="#10b981",
            bg=CARD_BG,
            padx=10,
            pady=3,
            relief="flat",
        )
        mode_badge.pack(side=tk.LEFT, padx=(0, 10))

        self.audio_btn = tk.Button(
            ctrl_box,
            text="🔔 Sound: ON",
            command=self._toggle_audio,
            bg=CARD_BG,
            fg=TEXT_MAIN,
            activebackground=CARD_BORDER,
            activeforeground=TEXT_MAIN,
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
        )
        self.audio_btn.pack(side=tk.LEFT, padx=8)

        self.clock_lbl = tk.Label(
            ctrl_box,
            text="🕒 00:00:00 IST",
            font=("Segoe UI", 10, "bold"),
            fg=ACCENT_BLUE,
            bg=CARD_BG,
            padx=12,
            pady=4,
            relief="flat",
        )
        self.clock_lbl.pack(side=tk.LEFT)

    def _build_metric_cards(self):
        """Renders the 6 top metric stat summary cards."""
        metrics_frame = tk.Frame(self.root, bg=BG_DARK, padx=16, pady=4)
        metrics_frame.pack(fill=tk.X)

        self.card_symbols_val = tk.StringVar(value="210")
        self.card_candles_val = tk.StringVar(value="0")
        self.card_signals_val = tk.StringVar(value="0")
        self.card_bullish_val = tk.StringVar(value="0")
        self.card_bearish_val = tk.StringVar(value="0")
        self.card_status_val = tk.StringVar(value="🟢 LIVE CONNECTED")

        cards_data = [
            ("F&O UNIVERSE", self.card_symbols_val, ACCENT_BLUE),
            ("5M CANDLES SCANNED", self.card_candles_val, "#e2e8f0"),
            ("TOTAL SIGNALS", self.card_signals_val, ACCENT_AMBER),
            ("BULLISH SETUPS", self.card_bullish_val, ACCENT_GREEN),
            ("BEARISH WARNINGS", self.card_bearish_val, ACCENT_RED),
            ("FEED STATUS", self.card_status_val, ACCENT_GREEN),
        ]

        for i, (title, var, color) in enumerate(cards_data):
            card = tk.Frame(metrics_frame, bg=CARD_BG, bd=1, relief="solid", highlightbackground=CARD_BORDER)
            card.pack(side=tk.LEFT, expand=True, fill=tk.BOTH, padx=4, pady=4)

            t_lbl = tk.Label(card, text=title, font=("Segoe UI", 7, "bold"), fg=TEXT_MUTED, bg=CARD_BG)
            t_lbl.pack(anchor="w", padx=10, pady=(6, 0))

            v_lbl = tk.Label(card, textvariable=var, font=("Segoe UI", 12, "bold"), fg=color, bg=CARD_BG)
            v_lbl.pack(anchor="w", padx=10, pady=(2, 6))

    def _build_notebook(self):
        """Creates the primary Tabbed interface (Tab 1: Signals, Tab 2: Market & Pivots)."""
        container = tk.Frame(self.root, bg=BG_DARK, padx=16, pady=8)
        container.pack(fill=tk.BOTH, expand=True)

        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # Tab 1: HEMA + T3 Strategy Scanner (Primary Active Strategy)
        self.tab_hema = tk.Frame(self.notebook, bg=BG_DARK)
        self.notebook.add(self.tab_hema, text="  🎯 HEMA + T3 Strategy Scanner  ")
        self._build_hema_tab()

        # Tab 2: Live Market & Pivots
        self.tab_market = tk.Frame(self.notebook, bg=BG_DARK)
        self.notebook.add(self.tab_market, text="  📊 Live Market & Daily Pivots (210 Stocks)  ")
        self._build_market_tab()

        # Tab 3: 5M Candlestick & CPR Chart
        self.tab_chart = tk.Frame(self.notebook, bg=BG_DARK)
        self.notebook.add(self.tab_chart, text="  📈 5M Candle & CPR Chart  ")
        self.chart_frame = CandleChartFrame(self.tab_chart, scanner=self.scanner, db_repo=self.db_repo)
        self.chart_frame.pack(fill=tk.BOTH, expand=True)

        # Tab 4: Chartink Intraday Screener (Live Multi-Strategy Breakout)
        self.tab_chartink = tk.Frame(self.notebook, bg=BG_DARK)
        self.notebook.add(self.tab_chartink, text="  🎯 Chartink Intraday (0)  ")
        self._build_chartink_tab()

        # 5-Minute Reversal Signals Tab (Disabled per user request; toggled via config.ENABLE_TAB1_REVERSAL_SIGNALS)
        self.tab_signals = tk.Frame(self.notebook, bg=BG_DARK)
        self._build_signals_tab()
        if getattr(config, "ENABLE_TAB1_REVERSAL_SIGNALS", False):
            self.notebook.add(self.tab_signals, text="  ⚡ 5-Minute Reversal Signals  ")

        # Tab Change Listener for instant, high-efficiency rendering
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _build_signals_tab(self):
        """Builds Tab 1 toolbar and signals treeview."""
        # Toolbar Container
        toolbar = tk.Frame(self.tab_signals, bg=BG_DARK, pady=6)
        toolbar.pack(fill=tk.X)

        # Row 1: Setup & Signal Confluence Filters (Direction, TF, Pattern, Status, CPR/Trap, Strict Zones)
        row1 = tk.Frame(toolbar, bg=BG_DARK, pady=2)
        row1.pack(fill=tk.X)

        # Direction Filter
        tk.Label(row1, text="Signal:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        dir_combo = ttk.Combobox(row1, textvariable=self.signal_direction_var, values=["ALL", "BULLISH SETUP", "BEARISH WARNING"], state="readonly", width=16)
        dir_combo.pack(side=tk.LEFT, padx=(0, 10))
        dir_combo.bind("<<ComboboxSelected>>", lambda e: self._render_signals())

        # Timeframe Filter
        tk.Label(row1, text="TF:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        tf_combo = ttk.Combobox(row1, textvariable=self.signal_tf_var, values=["ALL"] + [t for t in config.SCANNER_TIMEFRAMES], state="readonly", width=6)
        tf_combo.pack(side=tk.LEFT, padx=(0, 10))
        tf_combo.bind("<<ComboboxSelected>>", lambda e: self._render_signals())

        # Pattern Filter
        tk.Label(row1, text="Pattern:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        pat_combo = ttk.Combobox(row1, textvariable=self.signal_pattern_var, values=[
            "ALL",
            "BULLISH ENGULFING",
            "BEARISH ENGULFING",
            "BULLISH HARAMI",
            "BEARISH HARAMI",
            "HAMMER",
            "INVERSE HAMMER",
            "SHOOTING STAR",
            "HANGING MAN",
            "BULLISH MARUBOZU",
            "BEARISH MARUBOZU",
        ], state="readonly", width=20)
        pat_combo.pack(side=tk.LEFT, padx=(0, 10))
        pat_combo.bind("<<ComboboxSelected>>", lambda e: self._render_signals())

        # Status Filter (Trigger Confirmation)
        tk.Label(row1, text="Status:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        status_combo = ttk.Combobox(row1, textvariable=self.signal_status_var, values=[
            "ALL STATUS",
            "✅ Triggered Only",
            "⏳ Pending Only",
            "❌ Invalidated Only",
        ], state="readonly", width=18)
        status_combo.pack(side=tk.LEFT, padx=(0, 10))
        status_combo.bind("<<ComboboxSelected>>", lambda e: self._render_signals())

        # Multi-Select CPR / Trap Filter
        tk.Label(row1, text="CPR / Trap:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        signal_cpr_options = [
            "⚡ Narrow CPR (<= 0.21%)",
            "🪤 Narrow Trap Zones (<= 0.21%)",
            "🎯 Candlestick Pattern at CPR",
            "🎯 Bullish Pattern at CPR Support",
            "🎯 Bearish Pattern at CPR Resistance",
            "🚀 CPR Breakout (Bullish Close)",
            "💥 CPR Breakdown (Bearish Close)",
            "🚀 Bear Trap Breakout (<= 0.21%)",
            "💥 Bull Trap Breakdown (<= 0.21%)",
            "🐂 Bull Trap (<= 0.21%)",
            "🐻 Bear Trap (<= 0.21%)",
            "🛡️ S2 Support Bounce",
            "🛡️ R2 Resistance Rejection",
            "📌 Inside CPR Zone",
        ]
        self.signal_cpr_menu = MultiSelectDropdown(
            row1,
            title="CPR/Trap",
            options=signal_cpr_options,
            default_selected=[
                "🚀 Bear Trap Breakout (<= 0.21%)",
                "🎯 Bearish Pattern at CPR Resistance",
                "⚡ Narrow CPR (<= 0.21%)",
                "💥 CPR Breakdown (Bearish Close)",
                "🪤 Narrow Trap Zones (<= 0.21%)",
                "🎯 Candlestick Pattern at CPR",
            ],
            on_change_callback=self._render_signals
        )
        self.signal_cpr_menu.pack(side=tk.LEFT, padx=(0, 10))

        # Strict Zones Toggle (Enabled by default)
        strict_cb = tk.Checkbutton(
            row1,
            text="🎯 Strict Zones Only",
            variable=self.strict_zones_var,
            command=self._render_signals,
            bg=BG_DARK,
            fg="#38bdf8",
            selectcolor="#111827",
            activebackground=BG_DARK,
            activeforeground="#38bdf8",
            font=("Segoe UI", 9, "bold"),
        )
        strict_cb.pack(side=tk.LEFT, padx=(6, 0))

        # Row 2: Execution, Ranking & Tools (Liquidity, Volume, Score, Sort, Search, Export CSV)
        row2 = tk.Frame(toolbar, bg=BG_DARK, pady=2)
        row2.pack(fill=tk.X)

        # Liquidity Filter Dropdown
        tk.Label(row2, text="Liquid:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        liq_combo = ttk.Combobox(row2, textvariable=self.signal_liq_var, values=[
            "ALL LIQUIDITY",
            "🔥 Ultra Liquid Only",
            "💧 High+ (>=200 Cr)",
        ], state="readonly", width=18)
        liq_combo.pack(side=tk.LEFT, padx=(0, 10))
        liq_combo.bind("<<ComboboxSelected>>", lambda e: self._render_signals())

        # Volume Filter Dropdown
        tk.Label(row2, text="Vol:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        vol_combo = ttk.Combobox(row2, textvariable=self.signal_vol_var, values=[
            "ALL VOLUMES",
            "🔥 Vol >= 1.0x (Confirmed)",
            "⚡ Vol >= 1.5x (Surge)",
        ], state="readonly", width=20)
        vol_combo.pack(side=tk.LEFT, padx=(0, 10))
        vol_combo.bind("<<ComboboxSelected>>", lambda e: self._render_signals())

        # Score Filter Dropdown
        tk.Label(row2, text="Score:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        score_combo = ttk.Combobox(row2, textvariable=self.signal_score_var, values=[
            "ALL SCORES",
            "⭐ Score >= 6",
            "🔥 High Conviction (>= 7)",
        ], state="readonly", width=20)
        score_combo.pack(side=tk.LEFT, padx=(0, 10))
        score_combo.bind("<<ComboboxSelected>>", lambda e: self._render_signals())

        # Sort Dropdown
        tk.Label(row2, text="Sort:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        sort_combo = ttk.Combobox(row2, textvariable=self.signal_sort_var, values=[
            "⏱️ Time (Newest First)",
            "⏱️ Time (Oldest First)",
            "🔥 Most Liquid (Top Turnover)",
            "🎯 Score (Highest First)",
            "📊 Rel Vol (Highest First)",
            "🔤 Symbol (A to Z)",
            "💰 Price (Highest First)",
        ], state="readonly", width=24)
        sort_combo.pack(side=tk.LEFT, padx=(0, 10))
        sort_combo.bind("<<ComboboxSelected>>", lambda e: self._render_signals())

        # Search Box
        tk.Label(row2, text="🔍 Search:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        search_entry = tk.Entry(row2, textvariable=self.signal_search_var, bg=CARD_BG, fg=TEXT_MAIN, insertbackground=TEXT_MAIN, relief="flat", font=("Segoe UI", 9), width=20)
        search_entry.pack(side=tk.LEFT, padx=(0, 12), ipady=3)
        self.signal_search_var.trace_add("write", lambda *args: self._debounce("sig_search", 250, self._render_signals))

        # Export CSV Button (Packed to Right)
        export_btn = tk.Button(
            row2,
            text="📥 Export Signals CSV",
            command=self._export_signals_csv,
            bg=CARD_BG,
            fg=ACCENT_BLUE,
            activebackground=CARD_BORDER,
            activeforeground=TEXT_MAIN,
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=12,
            pady=3,
            cursor="hand2",
        )
        export_btn.pack(side=tk.RIGHT)

        # Dynamic Signals & Stocks Counter Badge
        self.signals_count_lbl = tk.Label(
            row2,
            text="📊 Showing: 0 Signals (0 Stocks)",
            font=("Segoe UI", 9, "bold"),
            fg="#38bdf8",
            bg=CARD_BG,
            padx=10,
            pady=3,
            relief="flat",
        )
        self.signals_count_lbl.pack(side=tk.RIGHT, padx=(0, 10))

        # Signals Treeview Frame
        tree_frame = tk.Frame(self.tab_signals, bg=BG_DARK)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        cols = [
            ("time", "Time", 75, "center"),
            ("symbol", "Symbol", 95, "w"),
            ("liquidity", "Liquidity", 85, "center"),
            ("tf", "TF", 50, "center"),
            ("signal", "Signal", 125, "w"),
            ("pattern", "Pattern", 140, "w"),
            ("status", "Trigger Status", 130, "center"),
            ("price", "Price (₹)", 85, "e"),
            ("score", "Score", 65, "center"),
            ("zone", "Pivot Zone", 150, "w"),
            ("pp", "PP", 75, "e"),
            ("pdh", "PDH", 75, "e"),
            ("pdl", "PDL", 75, "e"),
            ("r1", "R1", 75, "e"),
            ("s1", "S1", 75, "e"),
            ("rel_vol", "Rel Vol", 70, "e"),
            ("factors", "Factors & Conditions Met", 240, "w"),
        ]

        self.signals_tree = ttk.Treeview(
            tree_frame,
            columns=[c[0] for c in cols],
            show="headings",
            selectmode="browse",
        )

        for col_id, col_name, width, align in cols:
            self.signals_tree.heading(col_id, text=col_name, anchor=align, command=lambda c=col_id: self._on_signals_column_click(c))
            self.signals_tree.column(col_id, width=width, anchor=align, stretch=(col_id in ("factors", "zone", "status")))

        # Scrollbars
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.signals_tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.signals_tree.xview)
        self.signals_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.signals_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        # Configure Color Tags
        self.signals_tree.tag_configure("bullish", foreground=ACCENT_GREEN)
        self.signals_tree.tag_configure("bearish", foreground=ACCENT_RED)
        self.signals_tree.tag_configure("triggered", background="#064e3b", foreground="#34d399")
        self.signals_tree.tag_configure("pending", background="#332200", foreground="#fbbf24")
        self.signals_tree.tag_configure("invalidated", background="#2a1215", foreground="#94a3b8")
        self.signals_tree.tag_configure("high_score", background="#062e22", foreground="#34d399")
        self.signals_tree.tag_configure("narrow_cpr", background="#0c2d48", foreground=ACCENT_BLUE)
        self.signals_tree.tag_configure("most_liquid", background="#064e3b", foreground="#34d399")
        self.signals_tree.tag_configure("alt_row", background=TREE_ALT)
        self.signals_tree.bind("<Double-1>", self._on_signals_double_click)
        self._render_signals()

    def _build_market_tab(self):
        """Builds Tab 2 toolbar and 210-stock market pivots treeview."""
        # Toolbar
        toolbar = tk.Frame(self.tab_market, bg=BG_DARK, pady=6)
        toolbar.pack(fill=tk.X)

        # Market Multi-Select CPR / Trap Filter
        tk.Label(toolbar, text="Filter Stocks:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        market_cpr_options = [
            "⚡ Narrow CPR (<= 0.21%) Trending",
            "🪤 Narrow Trap Zones (<= 0.21%)",
            "🎯 Pattern at CPR Zone",
            "🚀 CPR Breakout (Bullish Close)",
            "💥 CPR Breakdown (Bearish Close)",
            "🐂 In Bull Trap (<= 0.21%)",
            "🐻 In Bear Trap (<= 0.21%)",
            "🛡️ At S2 Support Bounce",
            "🛡️ At R2 Resistance Rejection",
            "📌 Inside CPR Zone",
        ]
        self.market_cpr_menu = MultiSelectDropdown(
            toolbar,
            title="CPR/Trap",
            options=market_cpr_options,
            default_selected=[
                "⚡ Narrow CPR (<= 0.21%) Trending",
                "🪤 Narrow Trap Zones (<= 0.21%)",
                "🎯 Pattern at CPR Zone",
                "💥 CPR Breakdown (Bearish Close)",
            ],
            on_change_callback=self._render_market
        )
        self.market_cpr_menu.pack(side=tk.LEFT, padx=(0, 14))

        # Market Liquidity Filter
        tk.Label(toolbar, text="Liquid:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        m_liq_combo = ttk.Combobox(toolbar, textvariable=self.market_liq_var, values=[
            "ALL LIQUIDITY",
            "🔥 Ultra Liquid Only",
            "💧 High+ (>=200 Cr)",
        ], state="readonly", width=18)
        m_liq_combo.pack(side=tk.LEFT, padx=(0, 14))
        m_liq_combo.bind("<<ComboboxSelected>>", lambda e: self._render_market())

        # Market Sort Options
        tk.Label(toolbar, text="Sort:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        m_sort_combo = ttk.Combobox(toolbar, textvariable=self.market_sort_var, values=[
            "⚡ CPR % (Narrowest First)",
            "🔥 Turnover / Liquidity (Highest First)",
            "📈 Change % (Top Gainers)",
            "📉 Change % (Top Losers)",
            "📊 Volume (Highest First)",
            "🔤 Symbol (A to Z)",
            "💰 LTP (Highest First)",
        ], state="readonly", width=28)
        m_sort_combo.pack(side=tk.LEFT, padx=(0, 14))
        m_sort_combo.bind("<<ComboboxSelected>>", lambda e: self._render_market())

        # Search Box
        tk.Label(toolbar, text="🔍 Search:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        m_search_entry = tk.Entry(toolbar, textvariable=self.market_search_var, bg=CARD_BG, fg=TEXT_MAIN, insertbackground=TEXT_MAIN, relief="flat", font=("Segoe UI", 9), width=20)
        m_search_entry.pack(side=tk.LEFT, padx=(0, 12), ipady=3)
        self.market_search_var.trace_add("write", lambda *args: self._debounce("mkt_search", 250, self._render_market))

        # Export CSV Button
        export_btn = tk.Button(
            toolbar,
            text="📥 Export Market CSV",
            command=self._export_market_csv,
            bg=CARD_BG,
            fg=ACCENT_BLUE,
            activebackground=CARD_BORDER,
            activeforeground=TEXT_MAIN,
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=12,
            pady=3,
            cursor="hand2",
        )
        export_btn.pack(side=tk.RIGHT)

        # Dynamic Market Stocks Counter Badge
        self.market_count_lbl = tk.Label(
            toolbar,
            text="📊 Showing: 0 Stocks",
            font=("Segoe UI", 9, "bold"),
            fg="#38bdf8",
            bg=CARD_BG,
            padx=10,
            pady=3,
            relief="flat",
        )
        self.market_count_lbl.pack(side=tk.RIGHT, padx=(0, 10))

        # Market Treeview Frame
        tree_frame = tk.Frame(self.tab_market, bg=BG_DARK)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        m_cols = [
            ("symbol", "Symbol", 95, "w"),
            ("liquidity", "Liquidity", 85, "center"),
            ("turnover", "Turnover (Cr)", 95, "e"),
            ("ltp", "LTP (₹)", 85, "e"),
            ("chg", "Chg %", 75, "e"),
            ("volume", "Volume", 85, "e"),
            ("zone", "Pivot Zone", 160, "w"),
            ("pp", "PP", 80, "e"),
            ("tc", "TC", 75, "e"),
            ("bc", "BC", 75, "e"),
            ("cpr_pct", "CPR %", 80, "center"),
            ("r1", "R1", 75, "e"),
            ("r2", "R2", 75, "e"),
            ("r3", "R3", 75, "e"),
            ("s1", "S1", 75, "e"),
            ("s2", "S2", 75, "e"),
            ("s3", "S3", 75, "e"),
            ("pdo", "PDO", 75, "e"),
            ("pdh", "PDH", 75, "e"),
            ("pdl", "PDL", 75, "e"),
            ("pdc", "PDC", 75, "e"),
            ("updated", "Updated", 75, "center"),
        ]

        self.market_tree = ttk.Treeview(
            tree_frame,
            columns=[c[0] for c in m_cols],
            show="headings",
            selectmode="browse",
        )

        for col_id, col_name, width, align in m_cols:
            self.market_tree.heading(col_id, text=col_name, anchor=align, command=lambda c=col_id: self._on_market_column_click(c))
            self.market_tree.column(col_id, width=width, anchor=align, stretch=(col_id == "zone"))

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.market_tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.market_tree.xview)
        self.market_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.market_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        # Configure Color Tags
        self.market_tree.tag_configure("narrow_cpr", background="#0c2d48", foreground=ACCENT_BLUE)
        self.market_tree.tag_configure("trap_zone", background="#332200", foreground=ACCENT_AMBER)
        self.market_tree.tag_configure("up", foreground=ACCENT_GREEN)
        self.market_tree.tag_configure("down", foreground=ACCENT_RED)
        self.market_tree.tag_configure("most_liquid", background="#064e3b", foreground="#34d399")
        self.market_tree.tag_configure("alt_row", background=TREE_ALT)
        self.market_tree.bind("<Double-1>", self._on_market_double_click)
        self._render_market()

    def _on_signals_double_click(self, event):
        """Double clicking a signal row opens its 5M Candlestick & CPR chart in Tab 3."""
        sel = self.signals_tree.selection()
        if not sel:
            return
        item = self.signals_tree.item(sel[0])
        vals = item.get("values", [])
        if len(vals) >= 2:
            sym = str(vals[1]).strip()
            self.open_chart_for_symbol(sym)

    def _on_market_double_click(self, event):
        """Double clicking a stock row opens its 5M Candlestick & CPR chart in Tab 3."""
        sel = self.market_tree.selection()
        if not sel:
            return
        sym = str(sel[0]).strip()
        self.open_chart_for_symbol(sym)

    def open_chart_for_symbol(self, symbol: str):
        """Switches to Tab 3 and renders the Candlestick & CPR chart for the requested symbol."""
        if hasattr(self, "chart_frame") and symbol:
            self.chart_frame.set_symbol(symbol)
            if hasattr(self, "notebook") and hasattr(self, "tab_chart"):
                self.notebook.select(self.tab_chart)

    def _on_tab_changed(self, event=None):
        """Non-blocking tab change handler allowing instant 60 FPS notebook transitions."""
        # Defer rendering to the next event loop tick so the notebook tab switches instantly
        self.root.after(20, self._process_tab_change)

    def _process_tab_change(self):
        """Marks the newly selected tab as needing a render, letting _poll_data handle it."""
        try:
            if not hasattr(self, "notebook"):
                return
            cur = self.notebook.select()
            if hasattr(self, "tab_signals") and cur == str(self.tab_signals):
                self._signals_dirty = True
            elif hasattr(self, "tab_market") and cur == str(self.tab_market):
                self.market_dirty = True
            elif hasattr(self, "tab_chart") and cur == str(self.tab_chart):
                self.chart_dirty = True
            elif hasattr(self, "tab_hema") and cur == str(self.tab_hema):
                self.hema_dirty = True
            elif hasattr(self, "tab_chartink") and cur == str(self.tab_chartink):
                self.chartink_dirty = True
        except Exception:
            pass

    def _build_footer(self):
        """Builds bottom status bar."""
        footer = tk.Frame(self.root, bg=CARD_BG, padx=16, pady=4)
        footer.pack(fill=tk.X, side=tk.BOTTOM)

        self.footer_status_lbl = tk.Label(
            footer,
            text="Engine: 5-Minute Multi-Factor Reversal Scanner  •  Protobuf Feed: Active  •  Broker Candles: Upstox V2/V3 REST",
            font=("Segoe UI", 8),
            fg=TEXT_MUTED,
            bg=CARD_BG,
        )
        self.footer_status_lbl.pack(side=tk.LEFT)

        self.footer_sync_lbl = tk.Label(
            footer,
            text="Last sync: --:--:--",
            font=("Segoe UI", 8),
            fg=ACCENT_BLUE,
            bg=CARD_BG,
        )
        self.footer_sync_lbl.pack(side=tk.RIGHT)

    def _update_clock(self):
        """Updates the live IST clock every second."""
        now_ist = datetime.now(IST_TZ).strftime("%H:%M:%S")
        self.clock_lbl.config(text=f"🕒 {now_ist} IST")
        self.root.after(1000, self._update_clock)

    def _toggle_audio(self):
        """Toggles sound alerts on/off."""
        self.is_audio_enabled = not self.is_audio_enabled
        if self.is_audio_enabled:
            self.audio_btn.config(text="🔔 Sound: ON", fg=TEXT_MAIN)
        else:
            self.audio_btn.config(text="🔕 Sound: OFF", fg=TEXT_MUTED)

    def _play_alert(self, is_bullish=True):
        """Plays sound chime on Windows."""
        if not self.is_audio_enabled or winsound is None:
            return
        def _beep():
            try:
                if is_bullish:
                    winsound.Beep(880, 150)
                    winsound.Beep(1174, 200)
                else:
                    winsound.Beep(440, 150)
                    winsound.Beep(330, 200)
            except Exception:
                pass
        threading.Thread(target=_beep, daemon=True).start()

    def _poll_data(self):
        """Polls dashboard_state periodically with tab-aware, low-CPU rendering."""
        try:
            snapshot = dashboard_state.get_snapshot()
            stats = snapshot.get("stats", {})
            signals = snapshot.get("signals", [])
            market = snapshot.get("market", [])
            price_ver = snapshot.get("price_version", 0)

            # Update Metric Cards only when values change
            sym_count = str(stats.get("symbols_scanned", len(market)))
            if self.card_symbols_val.get() != sym_count:
                self.card_symbols_val.set(sym_count)
            candle_count = str(stats.get("candles_processed", 0))
            if self.card_candles_val.get() != candle_count:
                self.card_candles_val.set(candle_count)
            sig_count = str(len(signals))
            if self.card_signals_val.get() != sig_count:
                self.card_signals_val.set(sig_count)

            # Only recount bullish/bearish and mark dirty when signal count changed
            if len(signals) != len(self.cached_signals):
                if len(signals) > len(self.cached_signals) and len(self.cached_signals) > 0:
                    latest = signals[0]
                    is_bull = "BULLISH" in str(latest.get("direction", ""))
                    self._play_alert(is_bull)

                self.cached_signals = list(signals)
                bull_cnt = sum(1 for s in signals if "BULLISH" in str(s.get("direction", "")))
                bear_cnt = sum(1 for s in signals if "BEARISH" in str(s.get("direction", "")))
                self.card_bullish_val.set(str(bull_cnt))
                self.card_bearish_val.set(str(bear_cnt))
                self._signals_dirty = True

            hema_sigs = snapshot.get("hema_signals", [])
            if hema_sigs:
                if len(hema_sigs) != len(self.cached_hema_signals) or not self.cached_hema_signals:
                    self.cached_hema_signals = list(hema_sigs)
                    self.hema_dirty = True

            chartink_sigs = snapshot.get("chartink_signals", [])
            if chartink_sigs is not None:
                if chartink_sigs != self.cached_chartink_signals:
                    self.cached_chartink_signals = list(chartink_sigs)
                    self.chartink_dirty = True
                    if hasattr(self, "notebook") and hasattr(self, "tab_chartink"):
                        try:
                            self.notebook.tab(self.tab_chartink, text=f"  🎯 Chartink Intraday ({len(self.cached_chartink_signals)})  ")
                        except Exception:
                            pass

            ws_status = stats.get("ws_status", "INITIALIZING...")
            if ws_status == "CONNECTED":
                new_status = "🟢 LIVE CONNECTED"
            elif ws_status == "DRY_RUN":
                new_status = "🔵 DRY RUN COMPLETE"
            elif ws_status == "INITIALIZING...":
                new_status = "🟡 LOADING F&O DATA..."
            elif ws_status == "ERROR":
                new_status = "🔴 ERROR / CHECK TOKEN"
            else:
                new_status = f"🟡 {ws_status}"
            if self.card_status_val.get() != new_status:
                self.card_status_val.set(new_status)

            last_upd = stats.get("last_updated")
            if last_upd:
                self.footer_sync_lbl.config(text=f"Last sync: {last_upd}")

            # Check if market data or live prices changed
            now = time.time()
            if price_ver != self.last_price_version or len(market) != len(self.cached_market):
                self.last_price_version = price_ver
                self.cached_market = list(market)
                self.market_dirty = True
                # Only flag chart as dirty if the active chart symbol's price actually changed
                active_sym = getattr(self.chart_frame, "current_symbol", "") if hasattr(self, "chart_frame") else ""
                if active_sym:
                    active_item = next((m for m in market if m.get("symbol") == active_sym), None)
                    cur_ltp = active_item.get("ltp") if active_item else None
                    if cur_ltp != getattr(self, "_last_active_chart_ltp", None):
                        self._last_active_chart_ltp = cur_ltp
                        self.chart_dirty = True

            # Populate chart symbols once market data is available
            if hasattr(self, "chart_frame") and not getattr(self, "chart_symbols_loaded", False):
                if market:
                    sym_list = [m.get("symbol") for m in market if m.get("symbol")]
                    self.chart_frame.populate_symbols(sym_list)
                    self.chart_symbols_loaded = True

            # TAB-AWARE PERFORMANCE RENDERING (Never update invisible tabs!)
            if hasattr(self, "notebook"):
                try:
                    cur_tab = self.notebook.select()

                    # If on Signals tab: throttle updates to at most once every 1.0 second
                    if hasattr(self, "tab_signals") and cur_tab == str(self.tab_signals):
                        if getattr(self, "_signals_dirty", False) and (now - getattr(self, "_last_signals_render_time", 0.0) >= 1.0):
                            self._last_signals_render_time = now
                            self._signals_dirty = False
                            self._render_signals()

                    # If on Market tab: throttle updates to at most once every 1.5 seconds
                    elif hasattr(self, "tab_market") and cur_tab == str(self.tab_market):
                        if self.market_dirty and (now - self.last_market_render_time >= 1.5):
                            self.last_market_render_time = now
                            self.market_dirty = False
                            self._render_market()

                    # If on Chart tab: throttle redraws to at most once every 2.0 seconds
                    elif hasattr(self, "tab_chart") and cur_tab == str(self.tab_chart) and hasattr(self, "chart_frame"):
                        if self.chart_dirty and (now - self.last_chart_render_time >= 2.0):
                            self.last_chart_render_time = now
                            self.chart_dirty = False
                            self.chart_frame.redraw_chart()

                    # If on HEMA + T3 Strategy tab: throttle redraws to at most once every 1.5 seconds
                    elif hasattr(self, "tab_hema") and cur_tab == str(self.tab_hema):
                        if self.hema_dirty and (now - self.last_hema_render_time >= 1.5):
                            self.last_hema_render_time = now
                            self.hema_dirty = False
                            self._render_hema_signals()

                    # If on Chartink Intraday Screener tab: throttle redraws to at most once every 1.5 seconds
                    elif hasattr(self, "tab_chartink") and cur_tab == str(self.tab_chartink):
                        if self.chartink_dirty and (now - self.last_chartink_render_time >= 1.5):
                            self.last_chartink_render_time = now
                            self.chartink_dirty = False
                            self._render_chartink_signals()

                except Exception:
                    pass

        except Exception as e:
            logger.debug(f"Error in Tkinter poll loop: {e}")

        # Schedule next poll in 800ms (smooth, responsive, zero CPU lag)
        self.root.after(800, self._poll_data)

    def _render_signals(self):
        """Renders signals in Treeview according to active filters and sort order."""
        # Clear existing items quickly in single batch
        existing = self.signals_tree.get_children()
        if existing:
            self.signals_tree.delete(*existing)

        dir_filter = self.signal_direction_var.get()
        pat_filter = self.signal_pattern_var.get()
        search_query = self.signal_search_var.get().strip().upper()
        sort_mode = self.signal_sort_var.get()

        market_lookup = {str(m.get("symbol", "")): m for m in self.cached_market} if hasattr(self, "cached_market") and self.cached_market else {}

        filtered = []
        for s in self.cached_signals:
            direction = str(s.get("direction", ""))
            pattern = str(s.get("pattern", ""))
            symbol = str(s.get("symbol", ""))
            zone = str(s.get("zone", ""))
            conds = s.get("conditions_met", [])
            score = s.get("score", 0)

            # Apply Direction Filter
            if dir_filter != "ALL" and dir_filter not in direction:
                continue

            # Apply Timeframe Filter
            tf_filter = self.signal_tf_var.get() if hasattr(self, "signal_tf_var") else "ALL"
            sig_tf = str(s.get("timeframe", "5m")).lower()
            if tf_filter != "ALL" and tf_filter.lower() != sig_tf:
                continue

            # Apply Pattern Filter
            if pat_filter != "ALL" and pat_filter not in pattern:
                continue

            # Apply Trigger Status Filter
            status_filter = self.signal_status_var.get() if hasattr(self, "signal_status_var") else "ALL STATUS"
            trig_status = str(s.get("trigger_status", "PENDING")).upper()
            if "Triggered" in status_filter and trig_status != "TRIGGERED":
                continue
            elif "Pending" in status_filter and trig_status != "PENDING":
                continue
            elif "Invalidated" in status_filter and trig_status != "INVALIDATED":
                continue

            # Apply Volume Filter
            rel_vol = float(s.get("relative_volume", 1.0))
            if hasattr(self, "signal_vol_var"):
                v_mode = self.signal_vol_var.get()
                if ">= 1.5x" in v_mode and rel_vol < 1.5:
                    continue
                elif ">= 1.0x" in v_mode and rel_vol < 1.0:
                    continue

            # Apply Min Score Filter
            if hasattr(self, "signal_score_var"):
                sc_mode = self.signal_score_var.get()
                if ">= 7" in sc_mode and score < 7:
                    continue
                elif ">= 6" in sc_mode and score < 6:
                    continue

            # Apply Liquidity Filter
            liq_mode = self.signal_liq_var.get() if hasattr(self, "signal_liq_var") else "ALL LIQUIDITY"
            tier = str(s.get("liquidity_tier", ""))
            turnover = float(s.get("turnover_cr", 0.0))
            is_ultra = bool(s.get("is_most_liquid")) or "Ultra" in tier
            if "Ultra" in liq_mode and not is_ultra:
                continue
            elif "High+" in liq_mode and not (is_ultra or "High" in tier or turnover >= 200.0):
                continue

            # Determine CPR Width and Narrow Status
            cpr_width = float(s.get("cpr_width_pct", 0.0))
            is_narrow_cpr = bool(s.get("is_narrow_cpr", False)) or (0 < cpr_width <= 0.21) or any("Narrow CPR" in str(c) for c in conds) or "Narrow CPR" in zone
            if not is_narrow_cpr and not cpr_width and market_lookup:
                m_entry = market_lookup.get(symbol)
                if m_entry:
                    cpr_width = float(m_entry.get("cpr_width_pct", 0.0))
                    is_narrow_cpr = (cpr_width <= 0.21) or bool(m_entry.get("is_narrow_cpr"))

            is_narrow_trap = bool(s.get("is_narrow_trap_zone", False)) or any("Narrow Bull Trap" in str(c) or "Narrow Bear Trap" in str(c) for c in conds) or ("Narrow" in zone and "Trap" in zone)

            # CPR-based conditions
            has_cpr_pattern = "at CPR" in zone or any("at CPR" in str(c) or "at Narrow CPR" in str(c) for c in conds) or "Inside CPR" in zone
            has_cpr_bull = "CPR Support" in zone or any("CPR Support" in str(c) for c in conds)
            has_cpr_bear = "CPR Resistance" in zone or any("CPR Resistance" in str(c) for c in conds)
            has_cpr_breakout = "CPR Breakout" in zone or any("CPR Breakout" in str(c) for c in conds)
            has_cpr_breakdown = "CPR Breakdown" in zone or any("CPR Breakdown" in str(c) for c in conds)
            has_narrow_cpr = is_narrow_cpr
            is_trap = "Trap" in zone or any("Trap" in str(c) for c in conds)
            is_bull_trap = "Bull Trap" in zone or any("Bull Trap" in str(c) for c in conds)
            is_bear_trap = "Bear Trap" in zone or any("Bear Trap" in str(c) for c in conds)
            is_cpr_test = "CPR" in zone or any("CPR" in str(c) for c in conds)

            has_level_bounce = any(
                "Bounce near S1" in str(c)
                or "Bounce near S2" in str(c)
                or "Rejection near R1" in str(c)
                or "Rejection near R2" in str(c)
                for c in conds
            ) or ("Bounce near S2" in zone) or ("Rejection near R2" in zone)

            # Strict Zones Only Filter (Enabled by default!)
            # Intraday reversals are high-probability only in Strict Zones:
            # 1. Narrow CPR (<= 0.21%) for all CPR setups (pattern, breakout, breakdown, test)
            # 2. Narrow Trap Zones (<= 0.21%) for Bull/Bear traps
            # 3. Extreme S2 / R2 key reversal level bounces
            # Wide CPRs (> 0.20%) are strictly excluded when Strict Zones Only is active.
            is_strict_cpr = is_narrow_cpr and (has_cpr_pattern or has_cpr_bull or has_cpr_bear or has_cpr_breakout or has_cpr_breakdown or is_cpr_test)
            is_strict_trap = is_narrow_trap
            is_strict_level = has_level_bounce
            is_valid_strict_zone = is_strict_cpr or is_strict_trap or is_strict_level

            strict_active = bool(getattr(self, "strict_zones_var", None) and self.strict_zones_var.get())
            if strict_active:
                if not is_valid_strict_zone:
                    continue

            # Apply Multi-Select CPR / Trap Filter
            if hasattr(self, "signal_cpr_menu") and not self.signal_cpr_menu.is_all_selected():
                sel = self.signal_cpr_menu.get_selected()
                matched = False

                if "⚡ Narrow CPR (<= 0.21%)" in sel and is_narrow_cpr:
                    matched = True
                if "🪤 Narrow Trap Zones (<= 0.21%)" in sel and is_narrow_trap:
                    matched = True
                if "🎯 Candlestick Pattern at CPR" in sel and has_cpr_pattern:
                    if not strict_active or is_narrow_cpr:
                        matched = True
                if "🎯 Bullish Pattern at CPR Support" in sel and has_cpr_bull:
                    if not strict_active or is_narrow_cpr:
                        matched = True
                if "🎯 Bearish Pattern at CPR Resistance" in sel and has_cpr_bear:
                    if not strict_active or is_narrow_cpr:
                        matched = True
                if "🚀 CPR Breakout (Bullish Close)" in sel and has_cpr_breakout:
                    if not strict_active or is_narrow_cpr:
                        matched = True
                if "💥 CPR Breakdown (Bearish Close)" in sel and has_cpr_breakdown:
                    if not strict_active or is_narrow_cpr:
                        matched = True
                if "🚀 Bear Trap Breakout (<= 0.21%)" in sel and ("Bear Trap Breakout" in zone or any("Bear Trap Breakout" in str(c) for c in conds)):
                    matched = True
                if "💥 Bull Trap Breakdown (<= 0.21%)" in sel and ("Bull Trap Breakdown" in zone or any("Bull Trap Breakdown" in str(c) for c in conds)):
                    matched = True
                if "🐂 Bull Trap (<= 0.21%)" in sel and is_bull_trap and (not strict_active or is_narrow_trap):
                    matched = True
                if "🐻 Bear Trap (<= 0.21%)" in sel and is_bear_trap and (not strict_active or is_narrow_trap):
                    matched = True
                if "🛡️ S2 Support Bounce" in sel and is_bull and (any("Bounce near S2" in str(c) for c in conds) or "Bounce near S2" in zone):
                    matched = True
                if "🛡️ R2 Resistance Rejection" in sel and (not is_bull) and (any("Rejection near R2" in str(c) for c in conds) or "Rejection near R2" in zone):
                    matched = True
                if "📌 Inside CPR Zone" in sel and is_cpr_test:
                    if not strict_active or is_narrow_cpr:
                        matched = True

                if not matched:
                    continue

            # Apply Search Query
            if search_query:
                if search_query not in symbol and search_query not in pattern:
                    continue

            filtered.append(s)

        # Update dynamic count badge and tab label with number of signals and unique stocks
        num_signals = len(filtered)
        unique_stocks = len(set(s.get("symbol") for s in filtered if s.get("symbol")))
        if hasattr(self, "signals_count_lbl"):
            self.signals_count_lbl.config(text=f"📊 Showing: {num_signals} Signals ({unique_stocks} Stocks)")
        if hasattr(self, "notebook") and hasattr(self, "tab_signals"):
            try:
                self.notebook.tab(self.tab_signals, text=f"  ⚡ 5-Minute Reversal Signals ({num_signals})  ")
            except Exception:
                pass

        # Apply Sorting
        if "Newest First" in sort_mode:
            filtered.sort(key=lambda s: str(s.get("timestamp", "")), reverse=True)
        elif "Oldest First" in sort_mode:
            filtered.sort(key=lambda s: str(s.get("timestamp", "")), reverse=False)
        elif "Most Liquid" in sort_mode:
            filtered.sort(key=lambda s: (1 if s.get("is_most_liquid") else 0, float(s.get("turnover_cr", 0.0))), reverse=True)
        elif "Score" in sort_mode:
            filtered.sort(key=lambda s: (int(s.get("score", 0)), str(s.get("timestamp", ""))), reverse=True)
        elif "Rel Vol" in sort_mode:
            filtered.sort(key=lambda s: float(s.get("relative_volume", 0.0)), reverse=True)
        elif "Symbol" in sort_mode:
            filtered.sort(key=lambda s: str(s.get("symbol", "")))
        elif "Price" in sort_mode:
            filtered.sort(key=lambda s: float(s.get("price", 0.0)), reverse=True)

        for idx, s in enumerate(filtered):
            direction = str(s.get("direction", ""))
            pattern = str(s.get("pattern", ""))
            symbol = str(s.get("symbol", ""))
            zone = str(s.get("zone", ""))
            conds = s.get("conditions_met", [])
            score = s.get("score", 0)

            time_str = str(s.get("timestamp", "--"))
            if "T" in time_str:
                time_str = time_str.split("T")[1].split("+")[0].split(".")[0]

            is_bull = "BULLISH" in direction
            tags = ["bullish" if is_bull else "bearish"]
            if score >= 7:
                tags.append("high_score")
            if any("Narrow CPR" in str(c) for c in conds):
                tags.append("narrow_cpr")

            is_most_liquid = bool(s.get("is_most_liquid", False))
            liq_tier = str(s.get("liquidity_tier", "Normal"))
            if is_most_liquid or "Ultra" in liq_tier:
                tags.append("most_liquid")

            # Trigger Status value & tag
            trig_status = str(s.get("trigger_status", "PENDING")).upper()
            trig_time = str(s.get("trigger_time", ""))
            trig_price = float(s.get("trigger_price", s.get("candle_high" if is_bull else "candle_low", s.get("price", 0.0))))

            if trig_status == "TRIGGERED":
                status_text = f"✅ TRIGGERED ({trig_time})" if trig_time else "✅ TRIGGERED"
                tags.append("triggered")
            elif trig_status == "INVALIDATED":
                status_text = "❌ INVALIDATED"
                tags.append("invalidated")
            else:
                comp = ">" if is_bull else "<"
                status_text = f"⏳ PENDING ({comp}{trig_price:.2f})"
                tags.append("pending")

            if idx % 2 == 1:
                tags.append("alt_row")

            conds_str = " • ".join(conds) if conds else "Standard Setup"
            tf_str = str(s.get("timeframe", "5m")).upper()

            self.signals_tree.insert(
                "",
                tk.END,
                values=(
                    time_str,
                    symbol,
                    liq_tier,
                    tf_str,
                    direction,
                    pattern,
                    status_text,
                    f"{float(s.get('price', 0)):.2f}",
                    score,
                    zone,
                    f"{float(s.get('pp', 0)):.2f}",
                    f"{float(s.get('pdh', 0)):.2f}",
                    f"{float(s.get('pdl', 0)):.2f}",
                    f"{float(s.get('r1', 0)):.2f}",
                    f"{float(s.get('s1', 0)):.2f}",
                    f"{float(s.get('relative_volume', 1.0)):.2f}x",
                    conds_str,
                ),
                tags=tuple(tags),
            )

        if len(self.signals_tree.get_children()) == 0:
            if not self.cached_signals:
                self.signals_tree.insert("", tk.END, values=(
                    "--:--:--", "SCANNING...", "--", "ALL", "INITIALIZING", "Downloading Candles & Scanning Today's Setups...",
                    "--", "--", "--", "Loading Universe...", "--", "--", "--", "--", "--", "--", "Evaluating universe stocks in background..."
                ), tags=("narrow_cpr",))
            else:
                self.signals_tree.insert("", tk.END, values=(
                    "--:--:--", "--", "--", "--", "NO SIGNALS", "No reversal signals matching current filters.",
                    "--", "", "", "", "", "", "", "", "", "", "Try adjusting filters or search query."
                ))

    def _render_market(self):
        """Renders 210-stock market pivot data in Treeview according to filters and sort."""
        search_query = self.market_search_var.get().strip().upper()
        m_sort = self.market_sort_var.get()

        filtered_m = []
        for m in self.cached_market:
            symbol = str(m.get("symbol", ""))
            zone = str(m.get("zone", ""))
            cpr_width = float(m.get("cpr_width_pct", 0.0))
            is_narrow = cpr_width <= 0.21 or bool(m.get("is_narrow_cpr"))

            has_cpr_breakout = "CPR Breakout" in zone
            has_cpr_breakdown = "CPR Breakdown" in zone
            has_narrow_trap = ("Narrow" in zone and "Trap" in zone)

            # Apply Market Multi-Select CPR / Trap Filter
            if hasattr(self, "market_cpr_menu") and not self.market_cpr_menu.is_all_selected():
                sel = self.market_cpr_menu.get_selected()
                matched = False
                if "⚡ Narrow CPR (<= 0.21%) Trending" in sel and is_narrow:
                    matched = True
                if "🪤 Narrow Trap Zones (<= 0.21%)" in sel and has_narrow_trap:
                    matched = True
                if "🎯 Pattern at CPR Zone" in sel and ("Pattern at CPR" in zone or "Inside CPR" in zone or "CPR" in zone) and is_narrow:
                    matched = True
                if "🚀 CPR Breakout (Bullish Close)" in sel and has_cpr_breakout and is_narrow:
                    matched = True
                if "💥 CPR Breakdown (Bearish Close)" in sel and has_cpr_breakdown and is_narrow:
                    matched = True
                if "🐂 In Bull Trap (<= 0.21%)" in sel and "Bull Trap" in zone:
                    matched = True
                if "🐻 In Bear Trap (<= 0.21%)" in sel and "Bear Trap" in zone:
                    matched = True
                if "🛡️ At S2 Support Bounce" in sel and "Bounce near S2" in zone:
                    matched = True
                if "🛡️ At R2 Resistance Rejection" in sel and "Rejection near R2" in zone:
                    matched = True
                if "📌 Inside CPR Zone" in sel and "Inside CPR" in zone and is_narrow:
                    matched = True

                if not matched:
                    continue

            if search_query and search_query not in symbol:
                continue

            # Apply Market Liquidity Filter
            m_liq_mode = self.market_liq_var.get() if hasattr(self, "market_liq_var") else "ALL LIQUIDITY"
            m_tier = str(m.get("liquidity_tier", ""))
            m_turnover = float(m.get("turnover_cr", 0.0))
            m_is_ultra = bool(m.get("is_most_liquid")) or "Ultra" in m_tier
            if "Ultra" in m_liq_mode and not m_is_ultra:
                continue
            elif "High+" in m_liq_mode and not (m_is_ultra or "High" in m_tier or m_turnover >= 200.0):
                continue

            filtered_m.append(m)

        # Update dynamic market count badge and tab label
        total_market = len(self.cached_market) if hasattr(self, "cached_market") and self.cached_market else len(filtered_m)
        if hasattr(self, "market_count_lbl"):
            self.market_count_lbl.config(text=f"📊 Showing: {len(filtered_m)} / {total_market} Stocks")
        if hasattr(self, "notebook") and hasattr(self, "tab_market"):
            try:
                self.notebook.tab(self.tab_market, text=f"  📊 Live Market & Daily Pivots ({len(filtered_m)} Stocks)  ")
            except Exception:
                pass

        # Apply Sorting to Market data
        if "Narrowest First" in m_sort:
            filtered_m.sort(key=lambda x: float(x.get("cpr_width_pct", 999.0)))
        elif "Turnover" in m_sort or "Liquidity" in m_sort:
            filtered_m.sort(key=lambda x: (1 if x.get("is_most_liquid") else 0, float(x.get("turnover_cr", 0.0))), reverse=True)
        elif "Top Gainers" in m_sort:
            filtered_m.sort(key=lambda x: float(x.get("change_pct", -999.0)), reverse=True)
        elif "Top Losers" in m_sort:
            filtered_m.sort(key=lambda x: float(x.get("change_pct", 999.0)))
        elif "Volume" in m_sort:
            filtered_m.sort(key=lambda x: int(x.get("volume", 0)), reverse=True)
        elif "Symbol" in m_sort:
            filtered_m.sort(key=lambda x: str(x.get("symbol", "")))
        elif "LTP" in m_sort:
            filtered_m.sort(key=lambda x: float(x.get("ltp", 0.0)), reverse=True)

        existing_children = list(self.market_tree.get_children())
        target_syms = [str(m.get("symbol", "")) for m in filtered_m]

        if not target_syms:
            for item in existing_children:
                self.market_tree.delete(item)
            if not self.cached_market:
                self.market_tree.insert("", tk.END, values=(
                    "LOADING...", "--", "--", "--", "--", "--", "--", "--", "Calculating Daily Pivots & CPR for 210 stocks...",
                    "--", "--", "--", "--", "--", "--", "--", "--", "--", "--", "--", "--", "--", "--", "--"
                ), tags=("narrow_cpr",))
            else:
                self.market_tree.insert("", tk.END, values=(
                    "NONE", "--", "--", "--", "--", "--", "--", "--", "No instruments matching current search/filter.",
                    "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""
                ))
            return

        if not hasattr(self, "_market_row_cache"):
            self._market_row_cache = {}

        # Check if full rebuild is needed (structure/order changed or initial populate)
        needs_full_rebuild = (existing_children != target_syms)
        if needs_full_rebuild:
            self._market_row_cache.clear()
            for item in existing_children:
                self.market_tree.delete(item)

        for idx, m in enumerate(filtered_m):
            symbol = str(m.get("symbol", ""))
            zone = str(m.get("zone", ""))
            cpr_width = float(m.get("cpr_width_pct", 0.0))
            is_narrow = cpr_width <= 0.20

            chg = float(m.get("change_pct", 0.0))
            chg_str = f"+{chg:.2f}%" if chg > 0 else f"{chg:.2f}%"

            cpr_display = f"⚡ {cpr_width:.2f}%" if is_narrow else f"{cpr_width:.2f}%"

            tags = []
            if is_narrow:
                tags.append("narrow_cpr")
            elif "Trap" in zone:
                tags.append("trap_zone")
            elif chg > 0:
                tags.append("up")
            elif chg < 0:
                tags.append("down")

            is_most_liquid = bool(m.get("is_most_liquid", False))
            liq_tier = str(m.get("liquidity_tier", "Normal"))
            if is_most_liquid or "Ultra" in liq_tier:
                tags.append("most_liquid")

            if idx % 2 == 1:
                tags.append("alt_row")

            turnover_val = float(m.get("turnover_cr", 0.0))
            turnover_str = f"₹{turnover_val:,.1f} Cr" if turnover_val > 0 else "--"

            row_vals = (
                symbol,
                liq_tier,
                turnover_str,
                f"{float(m.get('ltp', 0)):.2f}",
                chg_str,
                f"{int(m.get('volume', 0)):,}",
                zone,
                f"{float(m.get('pp', 0)):.2f}",
                f"{float(m.get('tc', 0)):.2f}",
                f"{float(m.get('bc', 0)):.2f}",
                cpr_display,
                f"{float(m.get('r1', 0)):.2f}",
                f"{float(m.get('r2', 0)):.2f}",
                f"{float(m.get('r3', 0)):.2f}",
                f"{float(m.get('s1', 0)):.2f}",
                f"{float(m.get('s2', 0)):.2f}",
                f"{float(m.get('s3', 0)):.2f}",
                f"{float(m.get('pdo', 0)):.2f}",
                f"{float(m.get('pdh', 0)):.2f}",
                f"{float(m.get('pdl', 0)):.2f}",
                f"{float(m.get('pdc', 0)):.2f}",
                str(m.get("time", "--")),
            )

            tag_tuple = tuple(tags)
            if needs_full_rebuild:
                self.market_tree.insert("", tk.END, iid=symbol, values=row_vals, tags=tag_tuple)
                self._market_row_cache[symbol] = (row_vals, tag_tuple)
            else:
                cached = self._market_row_cache.get(symbol)
                if cached is None or cached[0] != row_vals or cached[1] != tag_tuple:
                    self.market_tree.item(symbol, values=row_vals, tags=tag_tuple)
                    self._market_row_cache[symbol] = (row_vals, tag_tuple)

    def _on_signals_column_click(self, col_id: str):
        """Toggles sort order when column heading is clicked in Signals tab."""
        if col_id == "time":
            if self.signal_sort_var.get() == "⏱️ Time (Newest First)":
                self.signal_sort_var.set("⏱️ Time (Oldest First)")
            else:
                self.signal_sort_var.set("⏱️ Time (Newest First)")
        elif col_id == "score":
            self.signal_sort_var.set("🔥 Score (Highest First)")
        elif col_id == "rel_vol":
            self.signal_sort_var.set("📊 Rel Vol (Highest First)")
        elif col_id == "symbol":
            self.signal_sort_var.set("🔤 Symbol (A to Z)")
        elif col_id == "price":
            self.signal_sort_var.set("💰 Price (Highest First)")
        self._render_signals()

    def _on_market_column_click(self, col_id: str):
        """Toggles sort order when column heading is clicked in Market tab."""
        if col_id in ("cpr_pct", "tc", "bc"):
            self.market_sort_var.set("⚡ CPR % (Narrowest First)")
        elif col_id == "chg":
            if self.market_sort_var.get() == "📈 Change % (Top Gainers)":
                self.market_sort_var.set("📉 Change % (Top Losers)")
            else:
                self.market_sort_var.set("📈 Change % (Top Gainers)")
        elif col_id == "volume":
            self.market_sort_var.set("📊 Volume (Highest First)")
        elif col_id == "symbol":
            self.market_sort_var.set("🔤 Symbol (A to Z)")
        elif col_id == "ltp":
            self.market_sort_var.set("💰 LTP (Highest First)")
        self._render_market()

    def _export_signals_csv(self):
        """Exports currently loaded signals into a CSV file."""
        if not self.cached_signals:
            messagebox.showinfo("Export CSV", "No signals available to export.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")],
            initialfile=f"fno_signals_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Time", "Symbol", "Liquidity", "Signal", "Pattern", "Trigger Status", "Trigger Time", "Setup High", "Setup Low", "Price", "Score", "Pivot Zone", "PP", "PDH", "PDL", "R1", "S1", "Rel Vol", "Conditions Met"])
                for s in self.cached_signals:
                    writer.writerow([
                        s.get("timestamp"),
                        s.get("symbol"),
                        s.get("liquidity_tier", "Normal"),
                        s.get("direction"),
                        s.get("pattern"),
                        s.get("trigger_status", "PENDING"),
                        s.get("trigger_time", ""),
                        s.get("candle_high", ""),
                        s.get("candle_low", ""),
                        s.get("price"),
                        s.get("score"),
                        s.get("zone"),
                        s.get("pp"),
                        s.get("pdh"),
                        s.get("pdl"),
                        s.get("r1"),
                        s.get("s1"),
                        s.get("relative_volume"),
                        "; ".join(s.get("conditions_met", [])),
                    ])
            messagebox.showinfo("Export Successful", f"Saved {len(self.cached_signals)} signals to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export Failed", f"Could not export CSV: {e}")

    def _export_market_csv(self):
        """Exports all 210 F&O stock pivots into a CSV file."""
        if not self.cached_market:
            messagebox.showinfo("Export CSV", "No market data available to export.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")],
            initialfile=f"fno_market_pivots_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Symbol", "Liquidity", "Turnover (Cr)", "LTP", "Change %", "Volume", "Zone", "PP", "TC", "BC", "CPR %", "R1", "R2", "R3", "S1", "S2", "S3", "PDO", "PDH", "PDL", "PDC", "Updated"])
                for m in self.cached_market:
                    writer.writerow([
                        m.get("symbol"),
                        m.get("liquidity_tier", "Normal"),
                        m.get("turnover_cr", 0.0),
                        m.get("ltp"),
                        m.get("change_pct"),
                        m.get("volume"),
                        m.get("zone"),
                        m.get("pp"),
                        m.get("tc"),
                        m.get("bc"),
                        m.get("cpr_width_pct"),
                        m.get("r1"),
                        m.get("r2"),
                        m.get("r3"),
                        m.get("s1"),
                        m.get("s2"),
                        m.get("s3"),
                        m.get("pdo"),
                        m.get("pdh"),
                        m.get("pdl"),
                        m.get("pdc"),
                        m.get("time"),
                    ])
            messagebox.showinfo("Export Successful", f"Saved {len(self.cached_market)} stock pivot records to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export Failed", f"Could not export CSV: {e}")

    def _on_market_mode_changed(self, event=None):
        """Switches active scanner mode between Nearest Future contracts, ATM Options, and Spot Equity."""
        selected_val = self.market_mode_var.get()
        if "OPTIONS" in selected_val:
            new_mode = "OPTIONS"
            mode_title = "ATM Options (Call / Put Contracts)"
        elif "SPOT" in selected_val:
            new_mode = "SPOT"
            mode_title = "Spot Equity (Cash EQ)"
        else:
            new_mode = "FUTURES"
            mode_title = "Nearest Futures Contract (FUT)"
        
        if not self.scanner:
            return

        if self.scanner.market_mode == new_mode:
            return

        confirm = messagebox.askyesno(
            "Switch Market Mode",
            f"Switch scanner mode to {mode_title}?\n\n"
            f"The scanner will refresh calculations and subscribe to {new_mode} mode.",
        )
        if not confirm:
            cur = self.scanner.market_mode
            display_map = {
                "FUTURES": "⚡ FUTURES (Nearest FUT)",
                "OPTIONS": "🎯 OPTIONS (ATM CE/PE)",
                "SPOT": "📈 SPOT (Cash EQ)",
            }
            self.market_mode_var.set(display_map.get(cur, "⚡ FUTURES (Nearest FUT)"))
            return

        def _do_switch():
            try:
                self.scanner.startup(force_refresh=False, mode=new_mode)
                self.chart_dirty = True
                self.market_dirty = True
                self.chartink_dirty = True
                self.hema_dirty = True
                if hasattr(self, "chart_frame") and self.chart_frame:
                    self.chart_frame.redraw_chart()
            except Exception as ex:
                logger.error(f"Error switching market mode to {new_mode}: {ex}")

        threading.Thread(target=_do_switch, daemon=True).start()

    def _on_universe_changed(self, event=None):
        """Switches active stock universe between F&O (210), Nifty 250, and Nifty 500."""
        selected_val = self.universe_var.get()
        if "500" in selected_val:
            new_univ = "NIFTY500"
            univ_title = "NIFTY 500 (Broad Market 500 Stocks)"
        elif "250" in selected_val:
            new_univ = "NIFTY250"
            univ_title = "NIFTY 250 (LargeMidcap 250 Stocks)"
        else:
            new_univ = "FNO"
            univ_title = "F&O Universe (210 Optionable Stocks)"

        if not self.scanner:
            return
        if getattr(self.scanner, "universe_name", "FNO") == new_univ:
            return

        confirm = messagebox.askyesno(
            "Switch Stock Universe",
            f"Switch stock universe to {univ_title}?\n\n"
            f"The scanner will load the {new_univ} universe, compute pivot levels, and stream real-time quotes.",
        )
        if not confirm:
            cur = getattr(self.scanner, "universe_name", getattr(config, "DEFAULT_UNIVERSE", "NIFTY500"))
            display_map = {
                "NIFTY500": "🌐 Nifty 500 (Broad Market)",
                "NIFTY250": "🏛️ Nifty 250 (LargeMidcap)",
                "FNO": "🔥 F&O Option Stocks (210)",
            }
            self.universe_var.set(display_map.get(cur, "🌐 Nifty 500 (Broad Market)"))
            return

        def _do_switch():
            try:
                self.scanner.startup(force_refresh=False, universe=new_univ)
                self.chart_dirty = True
                self.market_dirty = True
                self.chartink_dirty = True
                self.hema_dirty = True
                if hasattr(self, "chart_frame") and self.chart_frame:
                    self.chart_frame.redraw_chart()
            except Exception as ex:
                logger.error(f"Error switching universe to {new_univ}: {ex}")

        threading.Thread(target=_do_switch, daemon=True).start()

    def _build_hema_tab(self):
        """Builds Tab 4: HEMA + T3 Strict Buy Sell with Anti-Sideways Filter Scanner."""
        toolbar = tk.Frame(self.tab_hema, bg=BG_DARK, pady=6)
        toolbar.pack(fill=tk.X)

        # Row 1: Primary Setup Filters (Timeframe, Signal Action, Market Regime, Trend Score, Sideways Filter)
        row1 = tk.Frame(toolbar, bg=BG_DARK, pady=2)
        row1.pack(fill=tk.X)

        # Timeframe Filter
        tk.Label(row1, text="Timeframe:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        tf_combo = ttk.Combobox(
            row1,
            textvariable=self.hema_tf_var,
            values=["ALL", "15m", "30m", "1h", "2h", "4h", "1d"],
            state="readonly",
            width=7,
        )
        tf_combo.pack(side=tk.LEFT, padx=(0, 10))
        tf_combo.bind("<<ComboboxSelected>>", lambda e: self._render_hema_signals())

        # Signal Filter
        tk.Label(row1, text="Signal:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        sig_combo = ttk.Combobox(
            row1,
            textvariable=self.hema_signal_var,
            values=["ALL", "🟢 BUY (CALL Entry)", "🔴 SELL (PUT Entry)", "⚠️ SIDEWAYS / NO-TRADE", "HOLD"],
            state="readonly",
            width=22,
        )
        sig_combo.pack(side=tk.LEFT, padx=(0, 10))
        sig_combo.bind("<<ComboboxSelected>>", lambda e: self._render_hema_signals())

        # Market Regime Filter
        tk.Label(row1, text="Regime:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        reg_combo = ttk.Combobox(
            row1,
            textvariable=self.hema_regime_var,
            values=["ALL", "🚀 TRENDING", "⚠️ SIDEWAYS / NO-TRADE", "🛑 CHOP COOLDOWN"],
            state="readonly",
            width=22,
        )
        reg_combo.pack(side=tk.LEFT, padx=(0, 10))
        reg_combo.bind("<<ComboboxSelected>>", lambda e: self._render_hema_signals())

        # Trend Score Filter
        tk.Label(row1, text="Trend Score:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        score_combo = ttk.Combobox(
            row1,
            textvariable=self.hema_score_var,
            values=["ALL", "🔥 High Trend (>= 7)", "Moderate Trend (>= 5)"],
            state="readonly",
            width=18,
        )
        score_combo.pack(side=tk.LEFT, padx=(0, 10))
        score_combo.bind("<<ComboboxSelected>>", lambda e: self._render_hema_signals())

        # Sideways Score Filter
        tk.Label(row1, text="Sideways Check:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        side_combo = ttk.Combobox(
            row1,
            textvariable=self.hema_sideways_filter_var,
            values=["ALL", "🛡️ Trend Only (Sideways < 3)", "⚠️ High Sideways (>= 3)"],
            state="readonly",
            width=24,
        )
        side_combo.pack(side=tk.LEFT, padx=(0, 10))
        side_combo.bind("<<ComboboxSelected>>", lambda e: self._render_hema_signals())

        # Row 2: Secondary Controls (Search, Sort, Manual Multi-TF Scan, CSV Export, Live Counter)
        row2 = tk.Frame(toolbar, bg=BG_DARK, pady=2)
        row2.pack(fill=tk.X)

        search_entry = ttk.Entry(row2, textvariable=self.hema_search_var, width=15)
        search_entry.pack(side=tk.LEFT, padx=(0, 10))
        search_entry.bind("<KeyRelease>", lambda e: self._debounce("hema_search", 250, self._render_hema_signals))

        tk.Label(row2, text="Sort By:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        sort_combo = ttk.Combobox(
            row2,
            textvariable=self.hema_sort_var,
            values=[
                "⏱️ Time (Newest First)",
                "⏱️ Time (Oldest First)",
                "🔥 Trend Score (Highest First)",
                "⚠️ Sideways Score (Lowest First)",
                "🔤 Symbol (A to Z)",
            ],
            state="readonly",
            width=24,
        )
        sort_combo.pack(side=tk.LEFT, padx=(0, 10))
        sort_combo.bind("<<ComboboxSelected>>", lambda e: self._render_hema_signals())

        # Scan Multi-TF Button
        scan_btn = tk.Button(
            row2,
            text="🚀 Scan Multi-Timeframes",
            command=self._trigger_hema_scan,
            bg="#2563eb",
            fg="#ffffff",
            activebackground="#1d4ed8",
            activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=10,
            pady=3,
            cursor="hand2",
        )
        scan_btn.pack(side=tk.LEFT, padx=(0, 10))

        # Export CSV Button
        export_btn = tk.Button(
            row2,
            text="📥 Export HEMA CSV",
            command=self._export_hema_csv,
            bg=CARD_BG,
            fg=ACCENT_BLUE,
            activebackground=CARD_BORDER,
            activeforeground=TEXT_MAIN,
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=10,
            pady=3,
            cursor="hand2",
        )
        export_btn.pack(side=tk.RIGHT)

        # Dynamic Counter Badge
        self.hema_count_lbl = tk.Label(
            row2,
            text="🎯 Showing: 0 Signals (0 Stocks)",
            font=("Segoe UI", 9, "bold"),
            fg="#38bdf8",
            bg=CARD_BG,
            padx=10,
            pady=3,
            relief="flat",
        )
        self.hema_count_lbl.pack(side=tk.RIGHT, padx=(0, 10))

        # HEMA Treeview
        tree_frame = tk.Frame(self.tab_hema, bg=BG_DARK)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        h_cols = [
            ("time", "Time", 75, "center"),
            ("symbol", "Symbol", 90, "w"),
            ("tf", "TF", 55, "center"),
            ("signal", "Signal Action", 145, "center"),
            ("regime", "Market Regime", 155, "center"),
            ("price", "Price (₹)", 85, "e"),
            ("trend_score", "Trend (0-10)", 80, "center"),
            ("sideways_score", "Sideways (0-7)", 90, "center"),
            ("hema", "HEMA(9)", 80, "e"),
            ("t3_fast", "T3 Fast(13)", 80, "e"),
            ("t3_slow", "T3 Slow(16)", 80, "e"),
            ("adx", "ADX(14)", 65, "center"),
            ("atr_ratio", "ATR / MA", 75, "center"),
            ("slope", "Slope %", 70, "center"),
            ("comp", "Consol %", 75, "center"),
            ("vol", "Vol Surge", 75, "center"),
            ("reasons", "Confluence Factors & Anti-Sideways Check", 310, "w"),
        ]

        self.hema_tree = ttk.Treeview(
            tree_frame,
            columns=[c[0] for c in h_cols],
            show="headings",
            selectmode="browse",
        )

        for col_id, col_name, width, align in h_cols:
            self.hema_tree.heading(col_id, text=col_name, anchor=align)
            self.hema_tree.column(col_id, width=width, anchor=align, stretch=(col_id in ("reasons", "regime", "signal")))

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.hema_tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.hema_tree.xview)
        self.hema_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.hema_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        # Configure Color Tags
        self.hema_tree.tag_configure("buy", background="#064e3b", foreground="#34d399")
        self.hema_tree.tag_configure("sell", background="#4c0519", foreground="#fb7185")
        self.hema_tree.tag_configure("sideways", background="#3b2907", foreground="#fbbf24")
        self.hema_tree.tag_configure("chop", background="#2a1538", foreground="#c084fc")
        self.hema_tree.tag_configure("alt_row", background=TREE_ALT)

        self.hema_tree.bind("<Double-1>", self._on_hema_double_click)

    def _on_hema_double_click(self, event):
        """Double clicking a HEMA signal row opens its Candlestick & CPR chart in Tab 3."""
        sel = self.hema_tree.selection()
        if not sel:
            return
        item = self.hema_tree.item(sel[0])
        vals = item.get("values", [])
        if len(vals) >= 2:
            sym = str(vals[1]).strip()
            self.open_chart_for_symbol(sym)

    def _trigger_hema_scan(self, is_auto: bool = False):
        """Triggers ultra-fast parallel Numba scan across universe for selected or all timeframes."""
        if not self.scanner:
            if not is_auto:
                self.hema_count_lbl.config(text="⚠️ Scanner backend initializing...")
            return
        if getattr(self.scanner, "_is_hema_scanning", False):
            if not is_auto:
                self.hema_count_lbl.config(text="⏳ Scan already in progress...")
            return
        has_univ = hasattr(self.scanner, "_universe") and bool(self.scanner._universe)
        is_running = getattr(self.scanner, "_is_running", False)
        if not has_univ and not is_running:
            if not is_auto:
                self.hema_count_lbl.config(text="⏳ Scanner starting up, please wait...")
            return

        selected_tf = self.hema_tf_var.get()
        tfs = ["15m", "30m", "1h", "2h", "4h", "1d"] if selected_tf == "ALL" else [selected_tf]
        self.hema_count_lbl.config(text=f"🔄 Parallel Scanning {len(tfs)} TF(s)...")

        def _do_scan():
            try:
                res = self.scanner.scan_hema_universe(timeframes=tfs)
                if isinstance(res, tuple):
                    elapsed, n_tasks, n_sigs = res
                    self.root.after(0, lambda: self.hema_count_lbl.config(
                        text=f"⚡ Scanned {n_tasks} setups ({len(tfs)} TF) in {elapsed:.2f}s • {n_sigs} signals"
                    ))
                self.hema_dirty = True
            except Exception as ex:
                logger.error(f"Error executing HEMA scan: {ex}")

        threading.Thread(target=_do_scan, daemon=True, name="HemaScanWorker").start()

    def _render_hema_signals(self):
        """Renders filtered and sorted HEMA + T3 signals in Tab 4 Treeview with differential updates."""
        tf_filter = self.hema_tf_var.get()
        sig_filter = self.hema_signal_var.get()
        reg_filter = self.hema_regime_var.get()
        score_filter = self.hema_score_var.get()
        side_filter = self.hema_sideways_filter_var.get()
        search_q = self.hema_search_var.get().strip().upper()
        sort_by = self.hema_sort_var.get()

        filtered = []
        for s in self.cached_hema_signals:
            sym = str(s.get("symbol", "")).upper()
            if search_q and search_q not in sym:
                continue

            s_tf = str(s.get("timeframe", "")).lower()
            if tf_filter != "ALL":
                if s_tf != tf_filter.lower():
                    continue
            else:
                if s_tf not in {"15m", "30m", "1h", "2h", "4h", "1d"}:
                    continue

            s_type = str(s.get("signal_type", "")).upper()
            if sig_filter == "🟢 BUY (CALL Entry)" and "BUY" not in s_type:
                continue
            elif sig_filter == "🔴 SELL (PUT Entry)" and "SELL" not in s_type:
                continue
            elif sig_filter == "⚠️ SIDEWAYS / NO-TRADE" and "SIDEWAYS" not in s_type:
                continue
            elif sig_filter == "HOLD" and "HOLD" not in s_type:
                continue

            s_regime = str(s.get("regime", "")).upper()
            if reg_filter == "🚀 TRENDING" and "TRENDING" not in s_regime:
                continue
            elif reg_filter == "⚠️ SIDEWAYS / NO-TRADE" and "SIDEWAYS" not in s_regime:
                continue
            elif reg_filter == "🛑 CHOP COOLDOWN" and "CHOP" not in s_regime:
                continue

            t_score = int(s.get("trend_score", 0))
            if ">= 7" in score_filter and t_score < 7:
                continue
            elif ">= 5" in score_filter and t_score < 5:
                continue

            s_score = int(s.get("sideways_score", 0))
            if "Sideways < 3" in side_filter and s_score >= 3:
                continue
            elif "Sideways (>= 3)" in side_filter and s_score < 3:
                continue

            filtered.append(s)

        # Sorting
        if sort_by == "⏱️ Time (Newest First)":
            filtered.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
        elif sort_by == "⏱️ Time (Oldest First)":
            filtered.sort(key=lambda x: str(x.get("timestamp", "")))
        elif sort_by == "🔥 Trend Score (Highest First)":
            filtered.sort(key=lambda x: (x.get("trend_score", 0), str(x.get("timestamp", ""))), reverse=True)
        elif sort_by == "⚠️ Sideways Score (Lowest First)":
            filtered.sort(key=lambda x: (x.get("sideways_score", 99), str(x.get("timestamp", ""))))
        elif sort_by == "🔤 Symbol (A to Z)":
            filtered.sort(key=lambda x: str(x.get("symbol", "")))

        unique_syms = {s.get("symbol") for s in filtered if s.get("symbol")}
        self.hema_count_lbl.config(text=f"🎯 Showing: {len(filtered)} Signals ({len(unique_syms)} Stocks)")

        existing_children = list(self.hema_tree.get_children())
        target_ids = [f"{s.get('symbol')}_{s.get('timeframe')}_{idx}" for idx, s in enumerate(filtered)]

        if not target_ids:
            if existing_children:
                self.hema_tree.delete(*existing_children)
            if not self.cached_hema_signals:
                self.hema_tree.insert("", tk.END, values=(
                    "--:--:--", "AUTO-SCANNING", "--", "STREAMING", "Multi-timeframe scanner active (15m, 30m, 1h, 2h, 4h, 1d)...",
                    "--", "--", "--", "--", "--", "--", "--", "--", "--", "--", "--", "--", "Signals stream automatically. Click 'Scan' anytime to force instant re-scan."
                ), tags=("sideways",))
            else:
                self.hema_tree.insert("", tk.END, values=(
                    "--:--:--", "--", "--", "NO MATCH", "No HEMA+T3 signals matching selected timeframe or filters.",
                    "--", "--", "--", "--", "--", "--", "--", "--", "--", "--", "--", "--", "Try changing Timeframe dropdown to 'ALL' or resetting filters."
                ))
            return

        if not hasattr(self, "_hema_row_cache"):
            self._hema_row_cache = {}

        needs_full_rebuild = (existing_children != target_ids)
        if needs_full_rebuild:
            self._hema_row_cache.clear()
            if existing_children:
                self.hema_tree.delete(*existing_children)

        for idx, s in enumerate(filtered):
            sig_type = str(s.get("signal_type", ""))
            regime = str(s.get("regime", ""))
            tags = []

            if "BUY" in sig_type:
                tags.append("buy")
            elif "SELL" in sig_type:
                tags.append("sell")
            elif "SIDEWAYS" in regime or "SIDEWAYS" in sig_type:
                tags.append("sideways")
            elif "CHOP" in regime:
                tags.append("chop")

            if idx % 2 == 1:
                tags.append("alt_row")

            reasons_list = s.get("conditions_met", [])
            reasons_str = " • ".join(reasons_list) if reasons_list else "--"

            row_vals = (
                str(s.get("timestamp", "--")),
                str(s.get("symbol", "--")),
                str(s.get("timeframe", "--")).upper(),
                sig_type,
                regime,
                f"{float(s.get('price', 0.0)):.2f}",
                f"{int(s.get('trend_score', 0))}/10",
                f"{int(s.get('sideways_score', 0))}/7",
                f"{float(s.get('hema', 0.0)):.2f}",
                f"{float(s.get('t3_fast', 0.0)):.2f}",
                f"{float(s.get('t3_slow', 0.0)):.2f}",
                f"{float(s.get('adx', 0.0)):.1f}",
                f"{float(s.get('atr_ratio', 0.0)):.2f}x",
                f"{float(s.get('ema_slope_pct', 0.0)):+.3f}%",
                f"{float(s.get('consolidation_compression_pct', 0.0)):.2f}%",
                f"{float(s.get('volume_ratio', 0.0)):.1f}x",
                reasons_str,
            )
            tag_tuple = tuple(tags)
            row_id = target_ids[idx]

            if needs_full_rebuild:
                self.hema_tree.insert("", tk.END, iid=row_id, values=row_vals, tags=tag_tuple)
                self._hema_row_cache[row_id] = (row_vals, tag_tuple)
            else:
                cached = self._hema_row_cache.get(row_id)
                if cached is None or cached[0] != row_vals or cached[1] != tag_tuple:
                    self.hema_tree.item(row_id, values=row_vals, tags=tag_tuple)
                    self._hema_row_cache[row_id] = (row_vals, tag_tuple)

    def _export_hema_csv(self):
        """Exports currently loaded HEMA + T3 signals into a CSV file."""
        if not self.cached_hema_signals:
            messagebox.showinfo("Export CSV", "No HEMA + T3 signals available to export.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")],
            initialfile=f"hema_t3_signals_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Time", "Symbol", "Timeframe", "Signal Action", "Market Regime", "Price",
                    "Trend Score", "Sideways Score", "HEMA(9)", "T3 Fast(13)", "T3 Slow(16)",
                    "ADX(14)", "ATR/MA Ratio", "EMA Slope %", "Consolidation %", "Volume Ratio", "Confluence Factors"
                ])
                for s in self.cached_hema_signals:
                    writer.writerow([
                        s.get("timestamp"),
                        s.get("symbol"),
                        s.get("timeframe"),
                        s.get("signal_type"),
                        s.get("regime"),
                        s.get("price"),
                        s.get("trend_score"),
                        s.get("sideways_score"),
                        s.get("hema"),
                        s.get("t3_fast"),
                        s.get("t3_slow"),
                        s.get("adx"),
                        s.get("atr_ratio"),
                        s.get("ema_slope_pct"),
                        s.get("consolidation_compression_pct"),
                        s.get("volume_ratio"),
                        "; ".join(s.get("conditions_met", [])),
                    ])
            messagebox.showinfo("Export Successful", f"Saved {len(self.cached_hema_signals)} HEMA+T3 signals to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export Failed", f"Could not export CSV: {e}")

    def _build_chartink_tab(self):
        """Builds Tab: Chartink Intraday Screener (Formula from chartink.com/screener/intraday-screener-27102787)."""
        toolbar = tk.Frame(self.tab_chartink, bg=BG_DARK, pady=6)
        toolbar.pack(fill=tk.X)

        row1 = tk.Frame(toolbar, bg=BG_DARK, pady=2)
        row1.pack(fill=tk.X)

        # Strategy Sub-Filter
        tk.Label(row1, text="Strategy:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        strat_combo = ttk.Combobox(
            row1,
            textvariable=self.chartink_strategy_var,
            values=[
                "ALL STRATEGIES",
                "Monthly Breakout (Sub 1)",
                "20W High + 200 SMA (Sub 2)",
                "MA + RSI + Vol Surge (Sub 3)",
            ],
            state="readonly",
            width=26,
        )
        strat_combo.pack(side=tk.LEFT, padx=(0, 10))
        strat_combo.bind("<<ComboboxSelected>>", lambda e: self._render_chartink_signals())

        # Search Entry
        tk.Label(row1, text="Search:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        search_entry = ttk.Entry(row1, textvariable=self.chartink_search_var, width=14)
        search_entry.pack(side=tk.LEFT, padx=(0, 10))
        search_entry.bind("<KeyRelease>", lambda e: self._debounce("chartink_search", 250, self._render_chartink_signals))

        # Sort Combo
        tk.Label(row1, text="Sort By:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        sort_combo = ttk.Combobox(
            row1,
            textvariable=self.chartink_sort_var,
            values=[
                "⏱️ Time (Newest First)",
                "⏱️ Time (Oldest First)",
                "🚀 Pivot Diff % (Highest First)",
                "💰 Turnover (Highest First)",
                "📊 RSI(14) (Highest First)",
                "🔤 Symbol (A to Z)",
            ],
            state="readonly",
            width=24,
        )
        sort_combo.pack(side=tk.LEFT, padx=(0, 10))
        sort_combo.bind("<<ComboboxSelected>>", lambda e: self._render_chartink_signals())

        # Real-time Auto-Scan Indicator Badge
        self.chartink_live_badge = tk.Label(
            row1,
            text="🟢 LIVE AUTO-SCAN (10s)",
            font=("Segoe UI", 9, "bold"),
            fg="#10b981",
            bg="#064e3b",
            relief="flat",
            padx=10,
            pady=3,
        )
        self.chartink_live_badge.pack(side=tk.LEFT, padx=(0, 6))

        # Optional Manual Quick Refresh Button
        refresh_btn = tk.Button(
            row1,
            text="⚡ Refresh",
            command=self._trigger_chartink_scan,
            bg=CARD_BG,
            fg=TEXT_MAIN,
            activebackground=CARD_BORDER,
            activeforeground="#ffffff",
            font=("Segoe UI", 8, "bold"),
            relief="flat",
            padx=8,
            pady=3,
            cursor="hand2",
        )
        refresh_btn.pack(side=tk.LEFT, padx=(0, 10))

        # Export CSV Button
        export_btn = tk.Button(
            row1,
            text="📥 Export CSV",
            command=self._export_chartink_csv,
            bg=CARD_BG,
            fg=ACCENT_BLUE,
            activebackground=CARD_BORDER,
            activeforeground=TEXT_MAIN,
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=10,
            pady=3,
            cursor="hand2",
        )
        export_btn.pack(side=tk.RIGHT)

        # Dynamic Counter Badge
        self.chartink_count_lbl = tk.Label(
            row1,
            text="🎯 Showing: 0 Candidates",
            font=("Segoe UI", 9, "bold"),
            fg="#38bdf8",
            bg=CARD_BG,
            padx=10,
            pady=3,
            relief="flat",
        )
        self.chartink_count_lbl.pack(side=tk.RIGHT, padx=(0, 10))

        # Chartink Treeview
        tree_frame = tk.Frame(self.tab_chartink, bg=BG_DARK)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        c_cols = [
            ("time", "Time", 75, "center"),
            ("symbol", "Symbol", 90, "w"),
            ("price", "Price (₹)", 85, "e"),
            ("strategy", "Matched Sub-Strategy", 200, "w"),
            ("pivot_diff", "Pivot Clearance %", 105, "center"),
            ("turnover", "Turnover (Cr)", 95, "e"),
            ("rsi", "RSI(14)", 70, "center"),
            ("ma_crossed", "MA Crossed", 95, "center"),
            ("vol_surge", "Vol Surge", 80, "center"),
            ("reasons", "Chartink Confluence & Breakout Factors", 330, "w"),
        ]

        self.chartink_tree = ttk.Treeview(
            tree_frame,
            columns=[c[0] for c in c_cols],
            show="headings",
            selectmode="browse",
        )

        for col_id, col_name, width, align in c_cols:
            self.chartink_tree.heading(col_id, text=col_name, anchor=align)
            self.chartink_tree.column(col_id, width=width, anchor=align, stretch=(col_id in ("strategy", "reasons")))

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.chartink_tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.chartink_tree.xview)
        self.chartink_tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.chartink_tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        # Configure Color Tags
        self.chartink_tree.tag_configure("sub1", background="#064e3b", foreground="#34d399")
        self.chartink_tree.tag_configure("sub2", background="#1e3a8a", foreground="#60a5fa")
        self.chartink_tree.tag_configure("sub3", background="#4c1d95", foreground="#c084fc")
        self.chartink_tree.tag_configure("alt_row", background=TREE_ALT)

        self.chartink_tree.bind("<Double-1>", self._on_chartink_double_click)

    def _on_chartink_double_click(self, event):
        """Double clicking a Chartink candidate opens its Candlestick & CPR chart in Tab 3."""
        sel = self.chartink_tree.selection()
        if not sel:
            return
        item = self.chartink_tree.item(sel[0])
        vals = item.get("values", [])
        if len(vals) >= 2:
            sym = str(vals[1]).strip()
            self.open_chart_for_symbol(sym)

    def _trigger_chartink_scan(self, is_auto: bool = False):
        """Triggers ultra-fast parallel scan across universe evaluating the exact Chartink Screener conditions."""
        if not self.scanner:
            if not is_auto:
                self.chartink_count_lbl.config(text="⚠️ Scanner backend initializing...")
            return
        if getattr(self.scanner, "_is_chartink_scanning", False):
            return

        has_cache = hasattr(self.scanner, "_daily_dfs_cache") and bool(self.scanner._daily_dfs_cache)
        has_univ = hasattr(self.scanner, "_universe") and bool(self.scanner._universe)
        is_running = getattr(self.scanner, "_is_running", False)

        if not has_univ and not has_cache and not is_running:
            if not is_auto:
                self.chartink_count_lbl.config(text="⏳ Scanner starting up, please wait...")
            return

        if not is_auto:
            self.chartink_count_lbl.config(text="🔄 Scanning Chartink Formula...")

        def _do_scan():
            try:
                res = self.scanner.scan_chartink_universe()
                if isinstance(res, tuple):
                    elapsed, n_tasks, n_sigs = res
                    now_str = datetime.now().strftime("%H:%M:%S")
                    self.root.after(0, lambda: self.chartink_count_lbl.config(
                        text=f"🟢 Auto-Scanned ({now_str}) • {n_sigs} Candidates ({n_tasks} Stocks)"
                    ))
                self.chartink_dirty = True
            except Exception as ex:
                logger.error(f"Error executing Chartink scan: {ex}")

        threading.Thread(target=_do_scan, daemon=True, name="ChartinkScanWorker").start()

    def _render_chartink_signals(self):
        """Renders filtered and sorted Chartink screener breakout candidates in Treeview."""
        strat_filter = self.chartink_strategy_var.get()
        search_q = self.chartink_search_var.get().strip().upper()
        sort_by = self.chartink_sort_var.get()

        filtered = []
        for s in self.cached_chartink_signals:
            sym = str(s.get("symbol", "")).upper()
            if search_q and search_q not in sym:
                continue

            strat_tag = str(s.get("strategy_tag", ""))
            if strat_filter != "ALL STRATEGIES":
                if strat_filter not in strat_tag:
                    continue

            filtered.append(s)

        # Sorting
        if sort_by == "⏱️ Time (Newest First)":
            filtered.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
        elif sort_by == "⏱️ Time (Oldest First)":
            filtered.sort(key=lambda x: str(x.get("timestamp", "")))
        elif sort_by == "🚀 Pivot Diff % (Highest First)":
            filtered.sort(key=lambda x: float(x.get("median_pivot_diff_pct", 0.0)), reverse=True)
        elif sort_by == "💰 Turnover (Highest First)":
            filtered.sort(key=lambda x: float(x.get("turnover_cr", 0.0)), reverse=True)
        elif sort_by == "📊 RSI(14) (Highest First)":
            filtered.sort(key=lambda x: float(x.get("rsi_14", 0.0)), reverse=True)
        elif sort_by == "🔤 Symbol (A to Z)":
            filtered.sort(key=lambda x: str(x.get("symbol", "")))

        unique_syms = {s.get("symbol") for s in filtered if s.get("symbol")}
        self.chartink_count_lbl.config(text=f"🎯 Showing: {len(filtered)} Candidates ({len(unique_syms)} Stocks)")
        if hasattr(self, "notebook") and hasattr(self, "tab_chartink"):
            try:
                self.notebook.tab(self.tab_chartink, text=f"  🎯 Chartink Intraday ({len(filtered)})  ")
            except Exception:
                pass

        existing_children = list(self.chartink_tree.get_children())
        target_ids = [f"{s.get('symbol')}_{s.get('strategy_tag')}_{idx}" for idx, s in enumerate(filtered)]

        if not target_ids:
            if existing_children:
                self.chartink_tree.delete(*existing_children)
            if not self.cached_chartink_signals:
                now_str = datetime.now().strftime("%H:%M:%S")
                univ_name = getattr(self.scanner, 'universe_name', 'Universe') if self.scanner else 'Universe'
                self.chartink_tree.insert("", tk.END, values=(
                    now_str, "REAL-TIME ACTIVE", "--", "Continuous Background Scanner Monitoring (Sub 1, Sub 2, Sub 3)...",
                    "--", "--", "--", "--", "--", f"🟢 Auto-evaluating {univ_name} stocks every 10 seconds."
                ), tags=("sub1",))
            else:
                self.chartink_tree.insert("", tk.END, values=(
                    "--:--:--", "--", "--", "NO MATCH",
                    "--", "--", "--", "--", "--", "No breakout candidates matching active strategy filter."
                ))
            return

        if not hasattr(self, "_chartink_row_cache"):
            self._chartink_row_cache = {}

        needs_full_rebuild = (existing_children != target_ids)
        if needs_full_rebuild:
            self._chartink_row_cache.clear()
            if existing_children:
                self.chartink_tree.delete(*existing_children)

        for idx, s in enumerate(filtered):
            strat = str(s.get("strategy_tag", ""))
            tags = []
            if "Sub 1" in strat or "Monthly" in strat:
                tags.append("sub1")
            elif "Sub 2" in strat or "20W" in strat:
                tags.append("sub2")
            elif "Sub 3" in strat or "MA" in strat:
                tags.append("sub3")

            if idx % 2 == 1:
                tags.append("alt_row")

            row_vals = (
                str(s.get("timestamp", "--")),
                str(s.get("symbol", "--")),
                f"{float(s.get('price', 0.0)):.2f}",
                strat,
                f"+{float(s.get('median_pivot_diff_pct', 0.0)):.2f}%",
                f"₹{float(s.get('turnover_cr', 0.0)):.1f} Cr",
                f"{float(s.get('rsi_14', 0.0)):.1f}",
                str(s.get("ma_crossed_str", "--")),
                f"{float(s.get('vol_surge_ratio', 1.0)):.1f}x",
                str(s.get("reasons_str", "--")),
            )
            tag_tuple = tuple(tags)
            row_id = target_ids[idx]

            if needs_full_rebuild:
                self.chartink_tree.insert("", tk.END, iid=row_id, values=row_vals, tags=tag_tuple)
                self._chartink_row_cache[row_id] = (row_vals, tag_tuple)
            else:
                cached = self._chartink_row_cache.get(row_id)
                if cached is None or cached[0] != row_vals or cached[1] != tag_tuple:
                    self.chartink_tree.item(row_id, values=row_vals, tags=tag_tuple)
                    self._chartink_row_cache[row_id] = (row_vals, tag_tuple)

    def _export_chartink_csv(self):
        """Exports currently loaded Chartink candidates into a CSV file."""
        if not self.cached_chartink_signals:
            messagebox.showinfo("Export CSV", "No Chartink candidates available to export.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")],
            initialfile=f"chartink_intraday_breakout_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Time", "Symbol", "Price", "Strategy", "Pivot Clearance %",
                    "Turnover (Cr)", "RSI(14)", "MA Crossed", "Vol Surge Ratio", "Confluences"
                ])
                for s in self.cached_chartink_signals:
                    writer.writerow([
                        s.get("timestamp"),
                        s.get("symbol"),
                        s.get("price"),
                        s.get("strategy_tag"),
                        s.get("median_pivot_diff_pct"),
                        s.get("turnover_cr"),
                        s.get("rsi_14"),
                        s.get("ma_crossed_str"),
                        s.get("vol_surge_ratio"),
                        s.get("reasons_str"),
                    ])
            messagebox.showinfo("Export Successful", f"Saved {len(self.cached_chartink_signals)} Chartink breakout signals to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export Failed", f"Could not export CSV: {e}")
