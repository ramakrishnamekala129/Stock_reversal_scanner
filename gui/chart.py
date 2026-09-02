"""
Interactive 5-Minute Candlestick Chart with CPR, Trap Zones, and Live Price Levels.
Embedded directly into Tkinter using Matplotlib FigureCanvasTkAgg.
"""

import logging
from typing import Any, Dict, List, Optional
import tkinter as tk
from tkinter import ttk

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import matplotlib.patches as patches
import pandas as pd

import config

logger = logging.getLogger(__name__)

# Dark Theme Color Palette matching desktop app
BG_DARK = "#0b0f19"
PANEL_BG = "#111827"
GRID_COLOR = "#1f2937"
TEXT_COLOR = "#e2e8f0"
TEXT_MUTED = "#94a3b8"

BULL_COLOR = "#10b981"      # Emerald Green
BEAR_COLOR = "#ef4444"      # Crimson Red
CPR_PP_COLOR = "#f59e0b"    # Bright Amber / Gold
CPR_BOUND_COLOR = "#06b6d4" # Bright Cyan
BULL_TRAP_COLOR = "#f43f5e" # Rose
BEAR_TRAP_COLOR = "#10b981" # Emerald
PIVOT_R_COLOR = "#f87171"   # Light Red
PIVOT_S_COLOR = "#34d399"   # Light Green
PD_COLOR = "#fb923c"        # Orange
LTP_COLOR = "#38bdf8"       # Sky Blue Glow


class CandleChartFrame(tk.Frame):
    """
    Tkinter Frame containing an interactive 5-minute candlestick chart
    with CPR levels, Trap Zones, Standard Pivots, and Live Price line.
    """

    def __init__(self, parent, scanner=None, db_repo=None, **kwargs):
        super().__init__(parent, bg=BG_DARK, **kwargs)
        self.scanner = scanner
        self.db_repo = db_repo
        self.current_symbol = "RELIANCE"

        # Level display toggles
        self.show_cpr = tk.BooleanVar(value=True)
        self.show_trap = tk.BooleanVar(value=True)
        self.show_pivots = tk.BooleanVar(value=True)
        self.show_ltp = tk.BooleanVar(value=True)

        self._build_toolbar()
        self._build_figure()

    def _build_toolbar(self):
        """Builds top control bar for stock selection and layer toggles."""
        toolbar = tk.Frame(self, bg=PANEL_BG, pady=6, padx=12)
        toolbar.pack(fill=tk.X, side=tk.TOP)

        # Symbol Selector
        tk.Label(toolbar, text="Symbol:", font=("Segoe UI", 10, "bold"), fg=TEXT_MUTED, bg=PANEL_BG).pack(side=tk.LEFT, padx=(0, 6))
        self.symbol_var = tk.StringVar(value=self.current_symbol)
        self.symbol_combo = ttk.Combobox(
            toolbar,
            textvariable=self.symbol_var,
            width=16,
            font=("Segoe UI", 9, "bold"),
        )
        self.symbol_combo.pack(side=tk.LEFT, padx=(0, 16))
        self.symbol_combo.bind("<<ComboboxSelected>>", lambda e: self.on_symbol_selected())
        self.symbol_combo.bind("<Return>", lambda e: self.on_symbol_selected())

        # Layer Checkboxes
        cpr_cb = tk.Checkbutton(
            toolbar,
            text="Central Pivot Range (CPR)",
            variable=self.show_cpr,
            command=self.redraw_chart,
            bg=PANEL_BG,
            fg=CPR_PP_COLOR,
            selectcolor=BG_DARK,
            activebackground=PANEL_BG,
            activeforeground=CPR_PP_COLOR,
            font=("Segoe UI", 9, "bold"),
        )
        cpr_cb.pack(side=tk.LEFT, padx=(0, 10))

        trap_cb = tk.Checkbutton(
            toolbar,
            text="Trap Zones (R1-PDH / S1-PDL)",
            variable=self.show_trap,
            command=self.redraw_chart,
            bg=PANEL_BG,
            fg="#f43f5e",
            selectcolor=BG_DARK,
            activebackground=PANEL_BG,
            activeforeground="#f43f5e",
            font=("Segoe UI", 9, "bold"),
        )
        trap_cb.pack(side=tk.LEFT, padx=(0, 10))

        pivots_cb = tk.Checkbutton(
            toolbar,
            text="S/R Pivots (R1-R3, S1-S3)",
            variable=self.show_pivots,
            command=self.redraw_chart,
            bg=PANEL_BG,
            fg="#38bdf8",
            selectcolor=BG_DARK,
            activebackground=PANEL_BG,
            activeforeground="#38bdf8",
            font=("Segoe UI", 9, "bold"),
        )
        pivots_cb.pack(side=tk.LEFT, padx=(0, 10))

        ltp_cb = tk.Checkbutton(
            toolbar,
            text="Live Price Line",
            variable=self.show_ltp,
            command=self.redraw_chart,
            bg=PANEL_BG,
            fg="#fbbf24",
            selectcolor=BG_DARK,
            activebackground=PANEL_BG,
            activeforeground="#fbbf24",
            font=("Segoe UI", 9, "bold"),
        )
        ltp_cb.pack(side=tk.LEFT, padx=(0, 16))

        # Stock Information / Quick Badge on the right
        self.info_lbl = tk.Label(
            toolbar,
            text="Loading chart data...",
            font=("Segoe UI", 9, "bold"),
            fg=TEXT_COLOR,
            bg=PANEL_BG,
        )
        self.info_lbl.pack(side=tk.RIGHT, padx=4)

    def _build_figure(self):
        """Initializes the Matplotlib Figure and Tkinter Canvas."""
        # 2 Subplots: 75% for Price & Levels, 25% for Volume
        self.fig = Figure(figsize=(10, 6), dpi=100, facecolor=BG_DARK)
        self.gs = self.fig.add_gridspec(nrows=2, ncols=1, height_ratios=[4, 1], hspace=0.08)

        self.ax_main = self.fig.add_subplot(self.gs[0, 0])
        self.ax_vol = self.fig.add_subplot(self.gs[1, 0], sharex=self.ax_main)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget.pack(fill=tk.BOTH, expand=True)

    def populate_symbols(self, symbols: List[str]):
        """Populates the combobox dropdown with available F&O symbols."""
        if not symbols:
            return
        sorted_syms = sorted(symbols)
        self.symbol_combo["values"] = sorted_syms
        if self.current_symbol not in sorted_syms and sorted_syms:
            self.current_symbol = sorted_syms[0]
            self.symbol_var.set(self.current_symbol)

    def on_symbol_selected(self):
        """Triggered when user selects or types a symbol in the combobox."""
        sym = self.symbol_var.get().strip().upper()
        if sym:
            self.set_symbol(sym)

    def set_symbol(self, symbol: str):
        """Switches the active chart symbol and refreshes."""
        self.current_symbol = symbol.upper()
        self.symbol_var.set(self.current_symbol)
        self.redraw_chart()

    def get_candle_data(self, symbol: str) -> pd.DataFrame:
        """Retrieves 5-minute candle history for the requested symbol."""
        df = None
        # 1. Try from live CandleEngine
        if self.scanner and hasattr(self.scanner, "candle_engine"):
            try:
                df = self.scanner.candle_engine.get_candle_history_df(symbol)
            except Exception:
                df = None

        # 2. Try from SQLite database
        if (df is None or df.empty) and self.db_repo:
            try:
                df = self.db_repo.get_candles_by_symbol(symbol, limit=150)
            except Exception:
                df = None

        if df is None or df.empty:
            # Fallback synthetic frame for offline display
            df = pd.DataFrame([
                {"timestamp": "09:15", "open": 1000.0, "high": 1005.0, "low": 998.0, "close": 1003.0, "volume": 50000},
                {"timestamp": "09:20", "open": 1003.0, "high": 1008.0, "low": 1001.0, "close": 1006.0, "volume": 65000},
                {"timestamp": "09:25", "open": 1006.0, "high": 1010.0, "low": 1004.0, "close": 1005.0, "volume": 42000},
            ])
        return df

    def get_pivots_data(self, symbol: str) -> Optional[Any]:
        """Retrieves daily pivot levels for the symbol."""
        if self.scanner and hasattr(self.scanner, "_pivots"):
            return self.scanner._pivots.get(symbol)
        return None

    def get_market_item(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Retrieves latest market snapshot item for the symbol."""
        from web.state import dashboard_state
        snap = dashboard_state.get_snapshot()
        for item in snap.get("market", []):
            if item.get("symbol") == symbol:
                return item
        return None

    def redraw_chart(self):
        """Renders the candlesticks, volume, CPR, Trap Zones, and live price."""
        self.ax_main.clear()
        self.ax_vol.clear()

        sym = self.current_symbol
        df = self.get_candle_data(sym)
        pivots = self.get_pivots_data(sym)
        m_item = self.get_market_item(sym)

        # Style Subplots
        self.ax_main.set_facecolor(BG_DARK)
        self.ax_vol.set_facecolor(BG_DARK)
        self.ax_main.grid(True, linestyle="--", alpha=0.2, color=GRID_COLOR)
        self.ax_vol.grid(True, linestyle="--", alpha=0.2, color=GRID_COLOR)

        for spine in self.ax_main.spines.values():
            spine.set_color(GRID_COLOR)
        for spine in self.ax_vol.spines.values():
            spine.set_color(GRID_COLOR)

        self.ax_main.tick_params(colors=TEXT_MUTED, labelsize=9)
        self.ax_vol.tick_params(colors=TEXT_MUTED, labelsize=8)

        if df is None or df.empty:
            self.ax_main.text(0.5, 0.5, f"No 5-minute candle data found for {sym}", color=TEXT_MUTED, ha="center", va="center")
            self.canvas.draw_idle()
            return

        # Prepare X-Axis and Bars
        n_candles = len(df)
        x_indices = list(range(n_candles))
        timestamps = []
        for ts in df["timestamp"]:
            if hasattr(ts, "strftime"):
                timestamps.append(ts.strftime("%H:%M"))
            else:
                timestamps.append(str(ts).split("T")[-1][:5] if "T" in str(ts) else str(ts)[-8:-3])

        opens = df["open"].values
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        volumes = df["volume"].values if "volume" in df.columns else [0] * n_candles

        min_price = min(lows) if len(lows) else 0
        max_price = max(highs) if len(highs) else 100

        # Draw Candlesticks & Volume Bars
        body_width = 0.65
        wick_width = 1.2
        for i in range(n_candles):
            o, h, l, c, v = opens[i], highs[i], lows[i], closes[i], volumes[i]
            is_bull = c >= o
            color = BULL_COLOR if is_bull else BEAR_COLOR

            # Wick
            self.ax_main.plot([i, i], [l, h], color=color, linewidth=wick_width, zorder=3)

            # Candle Body
            bottom = min(o, c)
            height = abs(c - o)
            if height == 0:
                height = (h - l) * 0.05 if (h - l) > 0 else 0.1

            rect = patches.Rectangle(
                (i - body_width / 2, bottom),
                body_width,
                height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.8,
                zorder=4,
            )
            self.ax_main.add_patch(rect)

            # Volume Bar
            self.ax_vol.bar(i, v, color=color, alpha=0.65, width=body_width, zorder=2)

        # --- Draw Overlays: CPR & Trap Zones & Pivots ---
        all_levels = []
        if pivots:
            # 1. CPR (Central Pivot Range)
            if self.show_cpr.get():
                cpr_min = min(pivots.tc, pivots.bc)
                cpr_max = max(pivots.tc, pivots.bc)
                # Shaded CPR Zone
                self.ax_main.axhspan(cpr_min, cpr_max, color=CPR_BOUND_COLOR, alpha=0.12, zorder=1)

                # TC, PP, BC Lines
                self.ax_main.axhline(pivots.tc, color=CPR_BOUND_COLOR, linestyle="--", linewidth=1.2, alpha=0.85, zorder=2)
                self.ax_main.axhline(pivots.pp, color=CPR_PP_COLOR, linestyle="-", linewidth=1.5, alpha=0.95, zorder=2)
                self.ax_main.axhline(pivots.bc, color=CPR_BOUND_COLOR, linestyle="--", linewidth=1.2, alpha=0.85, zorder=2)

                # Level Label annotations
                cpr_tag = "Narrow CPR" if pivots.is_narrow_cpr else "CPR"
                self.ax_main.text(n_candles - 0.2, pivots.pp, f" PP {pivots.pp:.2f} ({cpr_tag})", color=CPR_PP_COLOR, va="center", fontsize=8, fontweight="bold")
                self.ax_main.text(n_candles - 0.2, pivots.tc, f" TC {pivots.tc:.2f}", color=CPR_BOUND_COLOR, va="center", fontsize=8)
                self.ax_main.text(n_candles - 0.2, pivots.bc, f" BC {pivots.bc:.2f}", color=CPR_BOUND_COLOR, va="center", fontsize=8)
                all_levels.extend([pivots.tc, pivots.pp, pivots.bc])

            # 2. Trap Zones (Bull Trap: R1-PDH, Bear Trap: S1-PDL)
            if self.show_trap.get():
                # Bull Trap Zone
                bt_min = min(pivots.r1, pivots.pdh)
                bt_max = max(pivots.r1, pivots.pdh)
                if abs(bt_max - bt_min) > 0:
                    self.ax_main.axhspan(bt_min, bt_max, color=BULL_TRAP_COLOR, alpha=0.14, zorder=1)
                    self.ax_main.text(0.5, bt_max, " 🪤 Bull Trap Zone (R1 - PDH)", color=BULL_TRAP_COLOR, va="bottom", fontsize=8, fontweight="bold")

                # Bear Trap Zone
                st_min = min(pivots.s1, pivots.pdl)
                st_max = max(pivots.s1, pivots.pdl)
                if abs(st_max - st_min) > 0:
                    self.ax_main.axhspan(st_min, st_max, color=BEAR_TRAP_COLOR, alpha=0.14, zorder=1)
                    self.ax_main.text(0.5, st_min, " 🪤 Bear Trap Zone (S1 - PDL)", color=BEAR_TRAP_COLOR, va="top", fontsize=8, fontweight="bold")
                all_levels.extend([bt_min, bt_max, st_min, st_max])

            # 3. Floor Pivots (R1-R3, S1-S3, PDH, PDL)
            if self.show_pivots.get():
                # R1, R2, R3
                for r_val, r_lbl in [(pivots.r1, "R1"), (pivots.r2, "R2"), (pivots.r3, "R3")]:
                    self.ax_main.axhline(r_val, color=PIVOT_R_COLOR, linestyle=":", linewidth=0.9, alpha=0.7, zorder=2)
                    self.ax_main.text(n_candles - 0.2, r_val, f" {r_lbl} {r_val:.2f}", color=PIVOT_R_COLOR, va="center", fontsize=8)
                    all_levels.append(r_val)

                # S1, S2, S3
                for s_val, s_lbl in [(pivots.s1, "S1"), (pivots.s2, "S2"), (pivots.s3, "S3")]:
                    self.ax_main.axhline(s_val, color=PIVOT_S_COLOR, linestyle=":", linewidth=0.9, alpha=0.7, zorder=2)
                    self.ax_main.text(n_candles - 0.2, s_val, f" {s_lbl} {s_val:.2f}", color=PIVOT_S_COLOR, va="center", fontsize=8)
                    all_levels.append(s_val)

                # PDH & PDL
                self.ax_main.axhline(pivots.pdh, color=PD_COLOR, linestyle="-.", linewidth=0.9, alpha=0.8, zorder=2)
                self.ax_main.text(0.5, pivots.pdh, f" PDH {pivots.pdh:.2f}", color=PD_COLOR, va="bottom", fontsize=8)
                self.ax_main.axhline(pivots.pdl, color=PD_COLOR, linestyle="-.", linewidth=0.9, alpha=0.8, zorder=2)
                self.ax_main.text(0.5, pivots.pdl, f" PDL {pivots.pdl:.2f}", color=PD_COLOR, va="top", fontsize=8)
                all_levels.extend([pivots.pdh, pivots.pdl])

        # 4. Live Price Line (LTP)
        ltp = None
        if m_item and m_item.get("ltp"):
            ltp = float(m_item["ltp"])
        elif len(closes):
            ltp = float(closes[-1])

        if ltp and self.show_ltp.get():
            self.ax_main.axhline(ltp, color=LTP_COLOR, linestyle=":", linewidth=1.4, alpha=0.95, zorder=5)
            # Glowing badge on right margin
            bbox_props = dict(boxstyle="round,pad=0.3", fc="#0284c7", ec="#38bdf8", lw=1.2)
            self.ax_main.text(
                n_candles - 0.2,
                ltp,
                f" LTP ₹{ltp:.2f} ",
                color="#ffffff",
                va="center",
                fontsize=8,
                fontweight="bold",
                bbox=bbox_props,
                zorder=6,
            )
            all_levels.append(ltp)

        # Set Dynamic Y-Limits
        valid_prices = [p for p in [min_price, max_price] + all_levels if p > 0]
        if valid_prices:
            y_min = min(valid_prices)
            y_max = max(valid_prices)
            pad = (y_max - y_min) * 0.06 if (y_max - y_min) > 0 else 5.0
            self.ax_main.set_ylim(y_min - pad, y_max + pad)

        # X-Axis Ticks (Time Formatting)
        step = max(1, n_candles // 8)
        tick_locs = list(range(0, n_candles, step))
        if (n_candles - 1) not in tick_locs:
            tick_locs.append(n_candles - 1)
        tick_lbls = [timestamps[idx] if idx < len(timestamps) else "" for idx in tick_locs]

        self.ax_vol.set_xticks(tick_locs)
        self.ax_vol.set_xticklabels(tick_lbls, rotation=0, color=TEXT_MUTED, fontsize=8)
        self.ax_main.tick_params(labelbottom=False)

        self.ax_main.set_xlim(-0.8, n_candles + 2.8)  # Leave room on the right for badges
        self.ax_vol.set_xlim(-0.8, n_candles + 2.8)

        # Update Top Info Bar Badge
        chg_pct = m_item.get("change_pct", 0.0) if m_item else 0.0
        chg_str = f"+{chg_pct:.2f}%" if chg_pct > 0 else f"{chg_pct:.2f}%"
        zone_str = m_item.get("zone", "Neutral") if m_item else "Active"
        cpr_w = pivots.cpr_width_pct if pivots else (m_item.get("cpr_width_pct", 0.0) if m_item else 0.0)
        cpr_type = "⚡ Narrow CPR" if (pivots and pivots.is_narrow_cpr) else "Standard CPR"

        info_text = f"{sym}  |  LTP: ₹{ltp:.2f} ({chg_str})  |  {cpr_type} ({cpr_w:.2f}%)  |  {zone_str}"
        self.info_lbl.config(text=info_text)

        try:
            self.fig.subplots_adjust(left=0.06, right=0.91, top=0.97, bottom=0.07, hspace=0.05)
        except Exception:
            pass
        self.canvas.draw_idle()
