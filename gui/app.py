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
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from typing import Any, Dict, List, Optional, Set

try:
    import winsound
except ImportError:
    winsound = None

import config
from web.state import dashboard_state

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


class ScannerTkinterGUI:
    """High-performance Tkinter Desktop GUI for Upstox F&O Intraday Scanner."""

    def __init__(self, root: tk.Tk, scanner=None):
        self.root = root
        self.scanner = scanner

        self.root.title("Upstox 5-Minute F&O Intraday Reversal Scanner - Live Desktop Dashboard")
        self.root.geometry("1480x880")
        self.root.minsize(1100, 700)
        self.root.configure(bg=BG_DARK)

        # Application State Tracking
        self.is_audio_enabled = True
        self.last_signal_count = 0
        self.last_market_hash = ""
        self.cached_signals: List[dict] = []
        self.cached_market: List[dict] = []

        # Filters & Sorting
        self.signal_direction_var = tk.StringVar(value="ALL")
        self.signal_pattern_var = tk.StringVar(value="ALL")
        self.signal_cpr_var = tk.StringVar(value="ALL")
        self.signal_search_var = tk.StringVar(value="")
        self.signal_sort_var = tk.StringVar(value="⏱️ Time (Newest First)")
        self.signals_sort_col = "time"
        self.signals_sort_rev = True

        self.market_cpr_var = tk.StringVar(value="ALL")
        self.market_search_var = tk.StringVar(value="")
        self.market_sort_var = tk.StringVar(value="⚡ CPR % (Narrowest First)")
        self.market_sort_col = "cpr_pct"
        self.market_sort_rev = False

        # Setup Styling & UI Components
        self._setup_styles()
        self._build_header()
        self._build_metric_cards()
        self._build_notebook()
        self._build_footer()

        # Start Real-Time Update Loop
        self._poll_data()
        self._update_clock()

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

        # Top Right Controls (Audio & Clock)
        ctrl_box = tk.Frame(header_frame, bg=BG_DARK)
        ctrl_box.pack(side=tk.RIGHT)

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

        # Tab 1: Signals Stream
        self.tab_signals = tk.Frame(self.notebook, bg=BG_DARK)
        self.notebook.add(self.tab_signals, text="  ⚡ 5-Minute Reversal Signals  ")
        self._build_signals_tab()

        # Tab 2: Live Market & Pivots
        self.tab_market = tk.Frame(self.notebook, bg=BG_DARK)
        self.notebook.add(self.tab_market, text="  📊 Live Market & Daily Pivots (210 Stocks)  ")
        self._build_market_tab()

    def _build_signals_tab(self):
        """Builds Tab 1 toolbar and signals treeview."""
        # Toolbar
        toolbar = tk.Frame(self.tab_signals, bg=BG_DARK, pady=8)
        toolbar.pack(fill=tk.X)

        # Direction Filter
        tk.Label(toolbar, text="Signal:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        dir_combo = ttk.Combobox(toolbar, textvariable=self.signal_direction_var, values=["ALL", "BULLISH SETUP", "BEARISH WARNING"], state="readonly", width=16)
        dir_combo.pack(side=tk.LEFT, padx=(0, 12))
        dir_combo.bind("<<ComboboxSelected>>", lambda e: self._render_signals())

        # Pattern Filter
        tk.Label(toolbar, text="Pattern:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        pat_combo = ttk.Combobox(toolbar, textvariable=self.signal_pattern_var, values=[
            "ALL",
            "BULLISH ENGULFING",
            "BEARISH ENGULFING",
            "BULLISH HARAMI",
            "BEARISH HARAMI",
            "HAMMER",
            "INVERSE HAMMER",
            "SHOOTING STAR",
            "HANGING MAN",
        ], state="readonly", width=20)
        pat_combo.pack(side=tk.LEFT, padx=(0, 12))
        pat_combo.bind("<<ComboboxSelected>>", lambda e: self._render_signals())

        # CPR Filter
        tk.Label(toolbar, text="CPR / Trap:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        cpr_combo = ttk.Combobox(toolbar, textvariable=self.signal_cpr_var, values=[
            "ALL",
            "🎯 Candlestick Pattern at CPR",
            "🎯 Bullish Pattern at CPR Support",
            "🎯 Bearish Pattern at CPR Resistance",
            "🚀 CPR Breakout (Bullish Close)",
            "💥 CPR Breakdown (Bearish Close)",
            "⚡ Narrow CPR (<= 0.1%)",
            "🪤 Narrow Trap Zones (<= 0.1%)",
            "🪤 All Trap Zones (R1-PDH / S1-PDL)",
            "🎯 Narrow CPR + Narrow Trap",
            "🐂 Bull Trap (R1 - PDH)",
            "🐻 Bear Trap (S1 - PDL)",
            "📌 Inside CPR / Pivot Test",
        ], state="readonly", width=34)
        cpr_combo.pack(side=tk.LEFT, padx=(0, 12))
        cpr_combo.bind("<<ComboboxSelected>>", lambda e: self._render_signals())

        # Sort Dropdown
        tk.Label(toolbar, text="Sort:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        sort_combo = ttk.Combobox(toolbar, textvariable=self.signal_sort_var, values=[
            "⏱️ Time (Newest First)",
            "⏱️ Time (Oldest First)",
            "🔥 Score (Highest First)",
            "📊 Rel Vol (Highest First)",
            "🔤 Symbol (A to Z)",
            "💰 Price (Highest First)",
        ], state="readonly", width=22)
        sort_combo.pack(side=tk.LEFT, padx=(0, 12))
        sort_combo.bind("<<ComboboxSelected>>", lambda e: self._render_signals())

        # Search Box
        tk.Label(toolbar, text="🔍 Search:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        search_entry = tk.Entry(toolbar, textvariable=self.signal_search_var, bg=CARD_BG, fg=TEXT_MAIN, insertbackground=TEXT_MAIN, relief="flat", font=("Segoe UI", 9), width=18)
        search_entry.pack(side=tk.LEFT, padx=(0, 12), ipady=3)
        self.signal_search_var.trace_add("write", lambda *args: self._render_signals())

        # Export CSV Button
        export_btn = tk.Button(
            toolbar,
            text="📥 Export Signals CSV",
            command=self._export_signals_csv,
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

        # Signals Treeview Frame
        tree_frame = tk.Frame(self.tab_signals, bg=BG_DARK)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        cols = [
            ("time", "Time", 75, "center"),
            ("symbol", "Symbol", 110, "w"),
            ("signal", "Signal", 130, "w"),
            ("pattern", "Pattern", 150, "w"),
            ("price", "Price (₹)", 85, "e"),
            ("score", "Score", 65, "center"),
            ("zone", "Pivot Zone", 160, "w"),
            ("pp", "PP", 80, "e"),
            ("pdh", "PDH", 80, "e"),
            ("pdl", "PDL", 80, "e"),
            ("r1", "R1", 80, "e"),
            ("s1", "S1", 80, "e"),
            ("rel_vol", "Rel Vol", 70, "e"),
            ("factors", "Factors & Conditions Met", 260, "w"),
        ]

        self.signals_tree = ttk.Treeview(
            tree_frame,
            columns=[c[0] for c in cols],
            show="headings",
            selectmode="browse",
        )

        for col_id, col_name, width, align in cols:
            self.signals_tree.heading(col_id, text=col_name, anchor=align, command=lambda c=col_id: self._on_signals_column_click(c))
            self.signals_tree.column(col_id, width=width, anchor=align, stretch=(col_id in ("factors", "zone")))

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
        self.signals_tree.tag_configure("high_score", background="#062e22", foreground="#34d399")
        self.signals_tree.tag_configure("narrow_cpr", background="#0c2d48", foreground=ACCENT_BLUE)
        self.signals_tree.tag_configure("alt_row", background=TREE_ALT)
        self._render_signals()

    def _build_market_tab(self):
        """Builds Tab 2 toolbar and 210-stock market pivots treeview."""
        # Toolbar
        toolbar = tk.Frame(self.tab_market, bg=BG_DARK, pady=8)
        toolbar.pack(fill=tk.X)

        # Market CPR Filter
        tk.Label(toolbar, text="Filter Stocks:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        m_cpr_combo = ttk.Combobox(toolbar, textvariable=self.market_cpr_var, values=[
            "ALL",
            "🎯 Pattern at CPR Zone",
            "🚀 CPR Breakout (Bullish Close)",
            "💥 CPR Breakdown (Bearish Close)",
            "⚡ Narrow CPR (<= 0.1%) Trending",
            "🪤 Narrow Trap Zones (<= 0.1%)",
            "🪤 In Trap Zones",
            "🎯 Narrow CPR + Narrow Trap",
            "🐂 In Bull Trap (R1 - PDH)",
            "🐻 In Bear Trap (S1 - PDL)",
            "📌 Inside CPR Zone",
        ], state="readonly", width=34)
        m_cpr_combo.pack(side=tk.LEFT, padx=(0, 12))
        m_cpr_combo.bind("<<ComboboxSelected>>", lambda e: self._render_market())

        # Market Sort Options
        tk.Label(toolbar, text="Sort:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        m_sort_combo = ttk.Combobox(toolbar, textvariable=self.market_sort_var, values=[
            "⚡ CPR % (Narrowest First)",
            "📈 Change % (Top Gainers)",
            "📉 Change % (Top Losers)",
            "📊 Volume (Highest First)",
            "🔤 Symbol (A to Z)",
            "💰 LTP (Highest First)",
        ], state="readonly", width=24)
        m_sort_combo.pack(side=tk.LEFT, padx=(0, 12))
        m_sort_combo.bind("<<ComboboxSelected>>", lambda e: self._render_market())

        # Search Box
        tk.Label(toolbar, text="🔍 Search:", font=("Segoe UI", 9, "bold"), fg=TEXT_MUTED, bg=BG_DARK).pack(side=tk.LEFT, padx=(0, 4))
        m_search_entry = tk.Entry(toolbar, textvariable=self.market_search_var, bg=CARD_BG, fg=TEXT_MAIN, insertbackground=TEXT_MAIN, relief="flat", font=("Segoe UI", 9), width=18)
        m_search_entry.pack(side=tk.LEFT, padx=(0, 12), ipady=3)
        self.market_search_var.trace_add("write", lambda *args: self._render_market())

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
            padx=10,
            pady=3,
            cursor="hand2",
        )
        export_btn.pack(side=tk.RIGHT)

        # Market Treeview Frame
        tree_frame = tk.Frame(self.tab_market, bg=BG_DARK)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        m_cols = [
            ("symbol", "Symbol", 100, "w"),
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
        self.market_tree.tag_configure("alt_row", background=TREE_ALT)
        self._render_market()

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
        """Polls dashboard_state periodically and updates GUI components."""
        try:
            snapshot = dashboard_state.get_snapshot()
            stats = snapshot.get("stats", {})
            signals = snapshot.get("signals", [])
            market = snapshot.get("market", [])

            # Update Metric Cards
            self.card_symbols_val.set(str(stats.get("symbols_scanned", len(market))))
            self.card_candles_val.set(str(stats.get("candles_processed", 0)))
            self.card_signals_val.set(str(len(signals)))
            self.card_bullish_val.set(str(stats.get("bullish_signals", 0)))
            ws_status = stats.get("ws_status", "INITIALIZING...")
            if ws_status == "CONNECTED":
                self.card_status_val.set("🟢 LIVE CONNECTED")
            elif ws_status == "DRY_RUN":
                self.card_status_val.set("🔵 DRY RUN COMPLETE")
            elif ws_status == "INITIALIZING...":
                self.card_status_val.set("🟡 LOADING F&O DATA...")
            elif ws_status == "ERROR":
                self.card_status_val.set("🔴 ERROR / CHECK TOKEN")
            else:
                self.card_status_val.set(f"🟡 {ws_status}")

            if stats.get("last_updated"):
                self.footer_sync_lbl.config(text=f"Last sync: {stats.get('last_updated')}")

            # Check if signals changed
            if len(signals) != len(self.cached_signals):
                if len(signals) > len(self.cached_signals) and len(self.cached_signals) > 0:
                    latest = signals[0]
                    is_bull = "BULLISH" in str(latest.get("direction", ""))
                    self._play_alert(is_bull)
                self.cached_signals = list(signals)
                self._render_signals()

            # Check if market data changed
            if len(market) != len(self.cached_market):
                self.cached_market = list(market)
                self._render_market()

        except Exception as e:
            logger.debug(f"Error in Tkinter poll loop: {e}")

        # Schedule next poll in 300ms
        self.root.after(300, self._poll_data)

    def _render_signals(self):
        """Renders signals in Treeview according to active filters and sort order."""
        # Clear existing items
        for item in self.signals_tree.get_children():
            self.signals_tree.delete(item)

        dir_filter = self.signal_direction_var.get()
        pat_filter = self.signal_pattern_var.get()
        cpr_filter = self.signal_cpr_var.get()
        search_query = self.signal_search_var.get().strip().upper()
        sort_mode = self.signal_sort_var.get()

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

            # Apply Pattern Filter
            if pat_filter != "ALL" and pattern != pat_filter:
                continue

            # Apply CPR / Trap Filter
            has_cpr_pattern = "at CPR" in zone or any("at CPR" in str(c) or "at Narrow CPR" in str(c) for c in conds) or "Inside CPR" in zone
            has_cpr_bull = "CPR Support" in zone or any("CPR Support" in str(c) for c in conds)
            has_cpr_bear = "CPR Resistance" in zone or any("CPR Resistance" in str(c) for c in conds)
            has_cpr_breakout = "CPR Breakout" in zone or any("CPR Breakout" in str(c) for c in conds)
            has_cpr_breakdown = "CPR Breakdown" in zone or any("CPR Breakdown" in str(c) for c in conds)
            has_narrow_cpr = any("Narrow CPR" in str(c) for c in conds) or "Narrow CPR" in zone
            has_narrow_trap = any("Narrow Bull Trap" in str(c) or "Narrow Bear Trap" in str(c) for c in conds) or ("Narrow" in zone and "Trap" in zone)
            is_trap = "Trap" in zone or any("Trap" in str(c) for c in conds)
            is_bull_trap = "Bull Trap" in zone or any("Bull Trap" in str(c) for c in conds)
            is_bear_trap = "Bear Trap" in zone or any("Bear Trap" in str(c) for c in conds)
            is_cpr_test = "CPR" in zone or any("CPR" in str(c) for c in conds)

            if cpr_filter == "🎯 Candlestick Pattern at CPR" and not has_cpr_pattern:
                continue
            elif cpr_filter == "🎯 Bullish Pattern at CPR Support" and not has_cpr_bull:
                continue
            elif cpr_filter == "🎯 Bearish Pattern at CPR Resistance" and not has_cpr_bear:
                continue
            elif cpr_filter == "🚀 CPR Breakout (Bullish Close)" and not has_cpr_breakout:
                continue
            elif cpr_filter == "💥 CPR Breakdown (Bearish Close)" and not has_cpr_breakdown:
                continue
            elif cpr_filter == "⚡ Narrow CPR (<= 0.1%)" and not has_narrow_cpr:
                continue
            elif cpr_filter == "🪤 Narrow Trap Zones (<= 0.1%)" and not has_narrow_trap:
                continue
            elif cpr_filter == "🪤 All Trap Zones (R1-PDH / S1-PDL)" and not is_trap:
                continue
            elif cpr_filter == "🎯 Narrow CPR + Narrow Trap" and not (has_narrow_cpr and (has_narrow_trap or is_trap)):
                continue
            elif cpr_filter == "🐂 Bull Trap (R1 - PDH)" and not is_bull_trap:
                continue
            elif cpr_filter == "🐻 Bear Trap (S1 - PDL)" and not is_bear_trap:
                continue
            elif cpr_filter == "📌 Inside CPR / Pivot Test" and not is_cpr_test:
                continue

            # Apply Search Query
            if search_query:
                if search_query not in symbol and search_query not in pattern:
                    continue

            filtered.append(s)

        # Apply Sorting
        if "Newest First" in sort_mode:
            filtered.sort(key=lambda s: str(s.get("timestamp", "")), reverse=True)
        elif "Oldest First" in sort_mode:
            filtered.sort(key=lambda s: str(s.get("timestamp", "")), reverse=False)
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
            if idx % 2 == 1:
                tags.append("alt_row")

            conds_str = " • ".join(conds) if conds else "Standard Setup"

            self.signals_tree.insert(
                "",
                tk.END,
                values=(
                    time_str,
                    symbol,
                    direction,
                    pattern,
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
                    "--:--:--", "SCANNING...", "INITIALIZING", "Downloading 5M Candles & Scanning Today's Setups...",
                    "--", "--", "Loading Universe...", "--", "--", "--", "--", "--", "--", "Evaluating 210 F&O stocks in background..."
                ), tags=("narrow_cpr",))
            else:
                self.signals_tree.insert("", tk.END, values=(
                    "--:--:--", "--", "NO SIGNALS", "No reversal signals matching current filters.",
                    "", "", "", "", "", "", "", "", "", "Try adjusting filters or search query."
                ))

    def _render_market(self):
        """Renders 210-stock market pivot data in Treeview according to filters and sort."""
        for item in self.market_tree.get_children():
            self.market_tree.delete(item)

        cpr_filter = self.market_cpr_var.get()
        search_query = self.market_search_var.get().strip().upper()
        m_sort = self.market_sort_var.get()

        filtered_m = []
        for m in self.cached_market:
            symbol = str(m.get("symbol", ""))
            zone = str(m.get("zone", ""))
            cpr_width = float(m.get("cpr_width_pct", 0.0))
            is_narrow = cpr_width <= 0.10

            has_cpr_breakout = "CPR Breakout" in zone
            has_cpr_breakdown = "CPR Breakdown" in zone
            has_narrow_trap = ("Narrow" in zone and "Trap" in zone)

            if cpr_filter == "🎯 Pattern at CPR Zone" and ("Pattern at CPR" not in zone and "Inside CPR" not in zone and "CPR" not in zone):
                continue
            elif cpr_filter == "🚀 CPR Breakout (Bullish Close)" and not has_cpr_breakout:
                continue
            elif cpr_filter == "💥 CPR Breakdown (Bearish Close)" and not has_cpr_breakdown:
                continue
            elif cpr_filter == "⚡ Narrow CPR (<= 0.1%) Trending" and not is_narrow:
                continue
            elif cpr_filter == "🪤 Narrow Trap Zones (<= 0.1%)" and not has_narrow_trap:
                continue
            elif cpr_filter == "🪤 In Trap Zones" and "Trap" not in zone:
                continue
            elif cpr_filter == "🎯 Narrow CPR + Narrow Trap" and not (is_narrow and ("Trap" in zone or has_narrow_trap)):
                continue
            elif cpr_filter == "🐂 In Bull Trap (R1 - PDH)" and "Bull Trap" not in zone:
                continue
            elif cpr_filter == "🐻 In Bear Trap (S1 - PDL)" and "Bear Trap" not in zone:
                continue
            elif cpr_filter == "📌 Inside CPR Zone" and "Inside CPR" not in zone:
                continue
            if search_query and search_query not in symbol:
                continue

            filtered_m.append(m)

        # Apply Sorting to Market data
        if "Narrowest First" in m_sort:
            filtered_m.sort(key=lambda x: float(x.get("cpr_width_pct", 999.0)))
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

        for idx, m in enumerate(filtered_m):
            symbol = str(m.get("symbol", ""))
            zone = str(m.get("zone", ""))
            cpr_width = float(m.get("cpr_width_pct", 0.0))
            is_narrow = cpr_width <= 0.10

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

            if idx % 2 == 1:
                tags.append("alt_row")

            self.market_tree.insert(
                "",
                tk.END,
                values=(
                    symbol,
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
                ),
                tags=tuple(tags),
            )

        if len(self.market_tree.get_children()) == 0:
            if not self.cached_market:
                self.market_tree.insert("", tk.END, values=(
                    "LOADING...", "--", "--", "--", "Calculating Daily Pivots & CPR for 210 stocks...",
                    "--", "--", "--", "--", "--", "--", "--", "--", "--", "--", "--", "--", "--", "--", "--"
                ), tags=("narrow_cpr",))
            else:
                self.market_tree.insert("", tk.END, values=(
                    "NONE", "--", "--", "--", "No instruments matching current search/filter.",
                    "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""
                ))

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
                writer.writerow(["Time", "Symbol", "Signal", "Pattern", "Price", "Score", "Pivot Zone", "PP", "PDH", "PDL", "R1", "S1", "Rel Vol", "Conditions Met"])
                for s in self.cached_signals:
                    writer.writerow([
                        s.get("timestamp"),
                        s.get("symbol"),
                        s.get("direction"),
                        s.get("pattern"),
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
                writer.writerow(["Symbol", "LTP", "Change %", "Volume", "Zone", "PP", "TC", "BC", "CPR %", "R1", "R2", "R3", "S1", "S2", "S3", "PDO", "PDH", "PDL", "PDC", "Updated"])
                for m in self.cached_market:
                    writer.writerow([
                        m.get("symbol"),
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
