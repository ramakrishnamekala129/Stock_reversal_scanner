"""
Interactive 5-Minute Candlestick Chart with CPR, Trap Zones, Signal Markers, and Live Price.
Embedded into Tkinter using Matplotlib FigureCanvasTkAgg with Auto-Fit Scaling and Zoom/Pan.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
import tkinter as tk
from tkinter import ttk

import warnings
warnings.filterwarnings("ignore", category=UserWarning, message=".*missing from font.*")

import matplotlib
matplotlib.use("TkAgg")
matplotlib.rcParams["font.sans-serif"] = ["Segoe UI", "DejaVu Sans", "Arial", "sans-serif"]
matplotlib.rcParams["font.family"] = "sans-serif"
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
TEXT_COLOR = "#f8fafc"
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
    Interactive 5-minute candlestick chart with:
    - Collision-free CPR & Trap Zone labeling
    - Auto-Fit Y-Axis scaling (prevents candle squashing)
    - Reversal Signal Markers (▲ Bullish / ▼ Bearish) directly on candles
    - Live forming candle tracking
    - Mouse wheel zoom & drag-to-pan + quick navigation toolbar
    """

    def __init__(self, parent, scanner=None, db_repo=None, **kwargs):
        super().__init__(parent, bg=BG_DARK, **kwargs)
        self.scanner = scanner
        self.db_repo = db_repo
        self.current_symbol = "RELIANCE"

        # Level display toggles
        self.auto_fit = tk.BooleanVar(value=True)
        self.show_cpr = tk.BooleanVar(value=True)
        self.show_trap = tk.BooleanVar(value=True)
        self.show_pivots = tk.BooleanVar(value=True)
        self.show_signals = tk.BooleanVar(value=True)
        self.show_ltp = tk.BooleanVar(value=True)

        # Pan / Zoom state tracking
        self._is_panning = False
        self._pan_start_x = 0
        self._custom_xlim: Optional[Tuple[float, float]] = None
        self._custom_ylim: Optional[Tuple[float, float]] = None

        self._build_toolbar()
        self._build_figure()
        self._bind_mouse_events()

    def _build_toolbar(self):
        """Builds top control bar for stock selection, layer toggles, and navigation buttons."""
        toolbar = tk.Frame(self, bg=PANEL_BG, pady=6, padx=12)
        toolbar.pack(fill=tk.X, side=tk.TOP)

        # Row 1: Symbol & Layers
        row1 = tk.Frame(toolbar, bg=PANEL_BG)
        row1.pack(fill=tk.X, side=tk.TOP, pady=(0, 4))

        # Symbol Selector
        tk.Label(row1, text="Symbol:", font=("Segoe UI", 10, "bold"), fg=TEXT_MUTED, bg=PANEL_BG).pack(side=tk.LEFT, padx=(0, 6))
        self.symbol_var = tk.StringVar(value=self.current_symbol)
        self.symbol_combo = ttk.Combobox(
            row1,
            textvariable=self.symbol_var,
            width=14,
            font=("Segoe UI", 9, "bold"),
        )
        self.symbol_combo.pack(side=tk.LEFT, padx=(0, 14))
        self.symbol_combo.bind("<<ComboboxSelected>>", lambda e: self.on_symbol_selected())
        self.symbol_combo.bind("<Return>", lambda e: self.on_symbol_selected())

        # Auto-Fit Scaling Toggle
        autofit_cb = tk.Checkbutton(
            row1,
            text="Auto-Fit Candles",
            variable=self.auto_fit,
            command=self._on_autofit_toggle,
            bg=PANEL_BG,
            fg="#38bdf8",
            selectcolor=BG_DARK,
            activebackground=PANEL_BG,
            activeforeground="#38bdf8",
            font=("Segoe UI", 9, "bold"),
        )
        autofit_cb.pack(side=tk.LEFT, padx=(0, 10))

        # CPR Checkbox
        cpr_cb = tk.Checkbutton(
            row1,
            text="CPR (TC, PP, BC)",
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

        # Trap Zones Checkbox
        trap_cb = tk.Checkbutton(
            row1,
            text="Trap Zones",
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

        # Pivots Checkbox
        pivots_cb = tk.Checkbutton(
            row1,
            text="Pivots (R1-R3, S1-S3)",
            variable=self.show_pivots,
            command=self.redraw_chart,
            bg=PANEL_BG,
            fg="#a78bfa",
            selectcolor=BG_DARK,
            activebackground=PANEL_BG,
            activeforeground="#a78bfa",
            font=("Segoe UI", 9, "bold"),
        )
        pivots_cb.pack(side=tk.LEFT, padx=(0, 10))

        # Reversal Signals on Chart Checkbox
        sig_cb = tk.Checkbutton(
            row1,
            text="🎯 Signals on Chart",
            variable=self.show_signals,
            command=self.redraw_chart,
            bg=PANEL_BG,
            fg="#34d399",
            selectcolor=BG_DARK,
            activebackground=PANEL_BG,
            activeforeground="#34d399",
            font=("Segoe UI", 9, "bold"),
        )
        sig_cb.pack(side=tk.LEFT, padx=(0, 10))

        # Live Price Line Checkbox
        ltp_cb = tk.Checkbutton(
            row1,
            text="Live Price",
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
            row1,
            text="Loading chart data...",
            font=("Segoe UI", 9, "bold"),
            fg=TEXT_COLOR,
            bg=PANEL_BG,
        )
        self.info_lbl.pack(side=tk.RIGHT, padx=4)

        # Row 2: Zoom, Pan & Ergonomics Buttons
        row2 = tk.Frame(toolbar, bg=PANEL_BG)
        row2.pack(fill=tk.X, side=tk.TOP)

        btn_style = dict(bg="#1f2937", fg=TEXT_COLOR, activebackground="#374151", activeforeground=TEXT_COLOR, font=("Segoe UI", 8, "bold"), relief="flat", padx=8, pady=2, cursor="hand2")

        tk.Button(row2, text="🔍 Zoom In (+)", command=self.zoom_in, **btn_style).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(row2, text="🔍 Zoom Out (-)", command=self.zoom_out, **btn_style).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(row2, text="◀ Pan Left", command=self.pan_left, **btn_style).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(row2, text="▶ Pan Right", command=self.pan_right, **btn_style).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(row2, text="⟲ Reset View", command=self.reset_view, **btn_style).pack(side=tk.LEFT, padx=(0, 12))

        tk.Label(
            row2,
            text="💡 Tip: Double-click any row in Signals or Market tab to inspect • Scroll wheel to zoom",
            font=("Segoe UI", 8),
            fg=TEXT_MUTED,
            bg=PANEL_BG,
        ).pack(side=tk.LEFT)

    def _build_figure(self):
        """Initializes the Matplotlib Figure and Tkinter Canvas."""
        self.fig = Figure(figsize=(10, 6), dpi=100, facecolor=BG_DARK)
        self.gs = self.fig.add_gridspec(nrows=2, ncols=1, height_ratios=[4, 1], hspace=0.06)

        self.ax_main = self.fig.add_subplot(self.gs[0, 0])
        self.ax_vol = self.fig.add_subplot(self.gs[1, 0], sharex=self.ax_main)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget.pack(fill=tk.BOTH, expand=True)

    def _bind_mouse_events(self):
        """Binds mouse scroll wheel and drag-to-pan events."""
        self.canvas.mpl_connect("scroll_event", self._on_mouse_scroll)
        self.canvas.mpl_connect("button_press_event", self._on_mouse_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_mouse_motion)
        self.canvas.mpl_connect("button_release_event", self._on_mouse_release)

    def _on_mouse_scroll(self, event):
        """Handles mouse wheel zooming centered at the cursor."""
        if event.inaxes != self.ax_main and event.inaxes != self.ax_vol:
            return
        base_scale = 1.25
        scale_factor = 1.0 / base_scale if event.button == "up" else base_scale

        cur_xlim = self.ax_main.get_xlim()
        xdata = event.xdata if event.xdata is not None else (cur_xlim[0] + cur_xlim[1]) / 2.0
        new_width = (cur_xlim[1] - cur_xlim[0]) * scale_factor
        rel_pos = (cur_xlim[1] - xdata) / (cur_xlim[1] - cur_xlim[0]) if (cur_xlim[1] - cur_xlim[0]) > 0 else 0.5
        new_xlim = (xdata - new_width * (1 - rel_pos), xdata + new_width * rel_pos)
        self._custom_xlim = new_xlim
        self.ax_main.set_xlim(new_xlim)
        self.canvas.draw_idle()

    def _on_mouse_press(self, event):
        if event.button == 1 and (event.inaxes == self.ax_main or event.inaxes == self.ax_vol):
            self._is_panning = True
            self._pan_start_x = event.xdata

    def _on_mouse_motion(self, event):
        if self._is_panning and event.xdata is not None and self._pan_start_x is not None:
            dx = self._pan_start_x - event.xdata
            cur_xlim = self.ax_main.get_xlim()
            new_xlim = (cur_xlim[0] + dx, cur_xlim[1] + dx)
            self._custom_xlim = new_xlim
            self.ax_main.set_xlim(new_xlim)
            self.canvas.draw_idle()

    def _on_mouse_release(self, event):
        self._is_panning = False

    def zoom_in(self):
        cur_xlim = self.ax_main.get_xlim()
        span = (cur_xlim[1] - cur_xlim[0]) * 0.75
        mid = (cur_xlim[0] + cur_xlim[1]) / 2.0
        self._custom_xlim = (mid - span / 2.0, mid + span / 2.0)
        self.ax_main.set_xlim(self._custom_xlim)
        self.canvas.draw_idle()

    def zoom_out(self):
        cur_xlim = self.ax_main.get_xlim()
        span = (cur_xlim[1] - cur_xlim[0]) * 1.35
        mid = (cur_xlim[0] + cur_xlim[1]) / 2.0
        self._custom_xlim = (mid - span / 2.0, mid + span / 2.0)
        self.ax_main.set_xlim(self._custom_xlim)
        self.canvas.draw_idle()

    def pan_left(self):
        cur_xlim = self.ax_main.get_xlim()
        shift = (cur_xlim[1] - cur_xlim[0]) * 0.25
        self._custom_xlim = (cur_xlim[0] - shift, cur_xlim[1] - shift)
        self.ax_main.set_xlim(self._custom_xlim)
        self.canvas.draw_idle()

    def pan_right(self):
        cur_xlim = self.ax_main.get_xlim()
        shift = (cur_xlim[1] - cur_xlim[0]) * 0.25
        self._custom_xlim = (cur_xlim[0] + shift, cur_xlim[1] + shift)
        self.ax_main.set_xlim(self._custom_xlim)
        self.canvas.draw_idle()

    def reset_view(self):
        self._custom_xlim = None
        self._custom_ylim = None
        self.redraw_chart()

    def _on_autofit_toggle(self):
        self._custom_ylim = None
        self.redraw_chart()

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
        self._custom_xlim = None
        self._custom_ylim = None
        self.redraw_chart()

    def get_candle_data(self, symbol: str) -> pd.DataFrame:
        """Retrieves 5-minute candle history for symbol, including currently forming live candle."""
        df = None
        # 1. Try from live CandleEngine with include_forming=True
        if self.scanner and hasattr(self.scanner, "candle_engine"):
            try:
                df = self.scanner.candle_engine.get_candle_history_df(symbol, include_forming=True)
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
                {"timestamp": "09:15", "open": 1000.0, "high": 1005.0, "low": 998.0, "close": 1003.0, "volume": 50000, "is_forming": False},
                {"timestamp": "09:20", "open": 1003.0, "high": 1008.0, "low": 1001.0, "close": 1006.0, "volume": 65000, "is_forming": False},
                {"timestamp": "09:25", "open": 1006.0, "high": 1010.0, "low": 1004.0, "close": 1005.0, "volume": 42000, "is_forming": False},
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

    def get_symbol_signals(self, symbol: str) -> List[Dict[str, Any]]:
        """Retrieves all reversal signals triggered for this symbol today."""
        from web.state import dashboard_state
        snap = dashboard_state.get_snapshot()
        matching = []
        for s in snap.get("signals", []):
            if s.get("symbol") == symbol:
                matching.append(s)
        return matching

    def redraw_chart(self):
        """Renders the complete candlestick chart with non-colliding labels and auto-fit scaling."""
        self.ax_main.clear()
        self.ax_vol.clear()

        sym = self.current_symbol
        df = self.get_candle_data(sym)
        pivots = self.get_pivots_data(sym)
        m_item = self.get_market_item(sym)
        signals = self.get_symbol_signals(sym)

        # Apply dark aesthetic
        self.ax_main.set_facecolor(BG_DARK)
        self.ax_vol.set_facecolor(BG_DARK)
        self.ax_main.grid(True, linestyle="--", alpha=0.18, color=GRID_COLOR)
        self.ax_vol.grid(True, linestyle="--", alpha=0.18, color=GRID_COLOR)

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

        n_candles = len(df)
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
        is_forming_flags = df["is_forming"].values if "is_forming" in df.columns else [False] * n_candles

        candle_min = min(lows) if len(lows) else 0
        candle_max = max(highs) if len(highs) else 100
        candle_range = max(candle_max - candle_min, candle_max * 0.005)

        # Draw Candlesticks & Volume Bars
        body_width = 0.70
        wick_width = 1.2
        for i in range(n_candles):
            o, h, l, c, v = opens[i], highs[i], lows[i], closes[i], volumes[i]
            is_forming = bool(is_forming_flags[i])
            is_bull = c >= o
            color = BULL_COLOR if is_bull else BEAR_COLOR

            # Candle Wick
            self.ax_main.plot([i, i], [l, h], color=color, linewidth=wick_width, zorder=3)

            # Candle Body
            bottom = min(o, c)
            height = abs(c - o)
            if height == 0:
                height = (h - l) * 0.05 if (h - l) > 0 else 0.05

            rect = patches.Rectangle(
                (i - body_width / 2, bottom),
                body_width,
                height,
                facecolor=color if not is_forming else "none",
                edgecolor=color,
                linewidth=1.2 if is_forming else 0.8,
                linestyle="--" if is_forming else "-",
                zorder=4,
            )
            self.ax_main.add_patch(rect)

            if is_forming:
                # Add pulse indicator above active forming candle
                self.ax_main.text(i, h + candle_range * 0.03, "LIVE", color="#38bdf8", fontsize=7, fontweight="bold", ha="center", zorder=5)

            # Volume Bar
            self.ax_vol.bar(i, v, color=color, alpha=0.60, width=body_width, zorder=2)

        # --- Draw Overlays: CPR, Trap Zones, Pivots, and Markers ---
        right_labels: List[Tuple[float, str, str, str]] = []  # (price, text, text_color, line_color)

        if pivots:
            # 1. CPR (Central Pivot Range)
            if self.show_cpr.get():
                cpr_min = min(pivots.tc, pivots.bc)
                cpr_max = max(pivots.tc, pivots.bc)
                self.ax_main.axhspan(cpr_min, cpr_max, color=CPR_BOUND_COLOR, alpha=0.12, zorder=1)

                self.ax_main.axhline(pivots.tc, color=CPR_BOUND_COLOR, linestyle="--", linewidth=1.2, alpha=0.85, zorder=2)
                self.ax_main.axhline(pivots.pp, color=CPR_PP_COLOR, linestyle="-", linewidth=1.6, alpha=0.95, zorder=2)
                self.ax_main.axhline(pivots.bc, color=CPR_BOUND_COLOR, linestyle="--", linewidth=1.2, alpha=0.85, zorder=2)

                cpr_tag = "Narrow" if pivots.is_narrow_cpr else "CPR"
                right_labels.append((pivots.pp, f"PP {pivots.pp:.2f} ({cpr_tag})", CPR_PP_COLOR, CPR_PP_COLOR))
                right_labels.append((pivots.tc, f"TC {pivots.tc:.2f}", CPR_BOUND_COLOR, CPR_BOUND_COLOR))
                right_labels.append((pivots.bc, f"BC {pivots.bc:.2f}", CPR_BOUND_COLOR, CPR_BOUND_COLOR))

            # 2. Trap Zones (Bull Trap: R1-PDH, Bear Trap: S1-PDL)
            if self.show_trap.get():
                # Bull Trap Zone
                bt_min = min(pivots.r1, pivots.pdh)
                bt_max = max(pivots.r1, pivots.pdh)
                if abs(bt_max - bt_min) > 0:
                    self.ax_main.axhspan(bt_min, bt_max, color=BULL_TRAP_COLOR, alpha=0.14, zorder=1)
                    # Place label neatly INSIDE shaded band on far left with badge
                    mid_y = (bt_min + bt_max) / 2.0
                    self.ax_main.text(
                        0.8,
                        mid_y,
                        " Bull Trap Zone (R1 - PDH) ",
                        color="#ffffff",
                        va="center",
                        fontsize=8,
                        fontweight="bold",
                        bbox=dict(boxstyle="round,pad=0.2", fc="#881337", ec="#f43f5e", lw=0.8, alpha=0.85),
                        zorder=3,
                    )

                # Bear Trap Zone
                st_min = min(pivots.s1, pivots.pdl)
                st_max = max(pivots.s1, pivots.pdl)
                if abs(st_max - st_min) > 0:
                    self.ax_main.axhspan(st_min, st_max, color=BEAR_TRAP_COLOR, alpha=0.14, zorder=1)
                    mid_y = (st_min + st_max) / 2.0
                    self.ax_main.text(
                        0.8,
                        mid_y,
                        " Bear Trap Zone (S1 - PDL) ",
                        color="#ffffff",
                        va="center",
                        fontsize=8,
                        fontweight="bold",
                        bbox=dict(boxstyle="round,pad=0.2", fc="#064e3b", ec="#10b981", lw=0.8, alpha=0.85),
                        zorder=3,
                    )

            # 3. Floor Pivots (R1-R3, S1-S3, PDH, PDL)
            if self.show_pivots.get():
                for r_val, r_lbl in [(pivots.r1, "R1"), (pivots.r2, "R2"), (pivots.r3, "R3")]:
                    self.ax_main.axhline(r_val, color=PIVOT_R_COLOR, linestyle=":", linewidth=0.9, alpha=0.7, zorder=2)
                    right_labels.append((r_val, f"{r_lbl} {r_val:.2f}", PIVOT_R_COLOR, PIVOT_R_COLOR))

                for s_val, s_lbl in [(pivots.s1, "S1"), (pivots.s2, "S2"), (pivots.s3, "S3")]:
                    self.ax_main.axhline(s_val, color=PIVOT_S_COLOR, linestyle=":", linewidth=0.9, alpha=0.7, zorder=2)
                    right_labels.append((s_val, f"{s_lbl} {s_val:.2f}", PIVOT_S_COLOR, PIVOT_S_COLOR))

                # Place PDH and PDL on LEFT side to prevent right-axis crowding
                self.ax_main.axhline(pivots.pdh, color=PD_COLOR, linestyle="-.", linewidth=0.9, alpha=0.8, zorder=2)
                self.ax_main.text(0.5, pivots.pdh, f" PDH {pivots.pdh:.2f}", color=PD_COLOR, va="bottom", fontsize=8, zorder=3)

                self.ax_main.axhline(pivots.pdl, color=PD_COLOR, linestyle="-.", linewidth=0.9, alpha=0.8, zorder=2)
                self.ax_main.text(0.5, pivots.pdl, f" PDL {pivots.pdl:.2f}", color=PD_COLOR, va="top", fontsize=8, zorder=3)

        # 4. Live Price Line (LTP)
        ltp = None
        if m_item and m_item.get("ltp"):
            ltp = float(m_item["ltp"])
        elif len(closes):
            ltp = float(closes[-1])

        if ltp and self.show_ltp.get():
            self.ax_main.axhline(ltp, color=LTP_COLOR, linestyle=":", linewidth=1.4, alpha=0.95, zorder=5)

        # --- 5. Render Reversal Signal Markers on Exact Candles ---
        if self.show_signals.get() and signals:
            for sig in signals:
                sig_ts = str(sig.get("timestamp", ""))
                sig_time_str = sig_ts.split("T")[-1][:5] if "T" in sig_ts else sig_ts[-8:-3]
                pattern = sig.get("pattern", "SIGNAL")
                direction = sig.get("direction", "")
                score = sig.get("score", 0)

                # Match signal time to candle index
                for c_idx in range(n_candles):
                    c_ts = timestamps[c_idx]
                    if c_ts == sig_time_str:
                        if "BULLISH" in direction:
                            # Plot Green Upward Marker below candle
                            marker_y = lows[c_idx] - (candle_range * 0.05)
                            self.ax_main.plot(c_idx, marker_y, marker="^", markersize=10, color=BULL_COLOR, zorder=6)
                            self.ax_main.text(
                                c_idx,
                                marker_y - (candle_range * 0.04),
                                f"{pattern}\n(+{score})",
                                color=BULL_COLOR,
                                fontsize=7,
                                fontweight="bold",
                                ha="center",
                                va="top",
                                zorder=6,
                            )
                        elif "BEARISH" in direction:
                            # Plot Red Downward Marker above candle
                            marker_y = highs[c_idx] + (candle_range * 0.05)
                            self.ax_main.plot(c_idx, marker_y, marker="v", markersize=10, color=BEAR_COLOR, zorder=6)
                            self.ax_main.text(
                                c_idx,
                                marker_y + (candle_range * 0.04),
                                f"{pattern}\n(+{score})",
                                color=BEAR_COLOR,
                                fontsize=7,
                                fontweight="bold",
                                ha="center",
                                va="bottom",
                                zorder=6,
                            )
                        break

        # --- 6. Collision-Free Right Label Placement ---
        # Sort labels by price and enforce minimum vertical distance
        right_labels.sort(key=lambda x: x[0])
        staggered_labels = []
        min_label_gap = candle_range * 0.035 if candle_range > 0 else 0.5
        last_y = -999999.0
        for price_val, text_val, text_col, line_col in right_labels:
            display_y = price_val
            if display_y - last_y < min_label_gap:
                display_y = last_y + min_label_gap
            staggered_labels.append((display_y, text_val, text_col))
            last_y = display_y

        # Right boundary for text labels
        x_label_pos = n_candles + 0.3
        for disp_y, text_val, text_col in staggered_labels:
            self.ax_main.text(
                x_label_pos,
                disp_y,
                f" {text_val}",
                color=text_col,
                va="center",
                fontsize=8,
                zorder=4,
            )

        # Place LTP Badge at the very end of right labels
        if ltp and self.show_ltp.get():
            bbox_props = dict(boxstyle="round,pad=0.3", fc="#0284c7", ec="#38bdf8", lw=1.2)
            self.ax_main.text(
                x_label_pos,
                ltp,
                f" LTP Rs {ltp:.2f} ",
                color="#ffffff",
                va="center",
                fontsize=8,
                fontweight="bold",
                bbox=bbox_props,
                zorder=7,
            )

        # --- 7. Y-Axis Limits (Auto-Fit vs Full Range) ---
        if self._custom_ylim is not None:
            self.ax_main.set_ylim(self._custom_ylim)
        elif self.auto_fit.get():
            # Focus tightly on active candles + immediate nearby levels
            pad = candle_range * 0.18
            nearby_levels = [p[0] for p in right_labels if (candle_min - candle_range * 0.6) <= p[0] <= (candle_max + candle_range * 0.6)]
            if ltp:
                nearby_levels.append(ltp)
            y_focus_min = min([candle_min] + nearby_levels) - pad
            y_focus_max = max([candle_max] + nearby_levels) + pad
            self.ax_main.set_ylim(y_focus_min, y_focus_max)
        else:
            # Full Range (All S/R Levels)
            all_plotted = [candle_min, candle_max] + [p[0] for p in right_labels]
            if ltp:
                all_plotted.append(ltp)
            valid = [p for p in all_plotted if p > 0]
            if valid:
                y_min, y_max = min(valid), max(valid)
                pad = (y_max - y_min) * 0.05
                self.ax_main.set_ylim(y_min - pad, y_max + pad)

        # --- 8. X-Axis Limits & Time Ticks (Session Ergonomics) ---
        min_slots = max(n_candles + 5, 36)  # Keep consistent candle proportions
        if self._custom_xlim is not None:
            self.ax_main.set_xlim(self._custom_xlim)
            self.ax_vol.set_xlim(self._custom_xlim)
        else:
            self.ax_main.set_xlim(-0.8, min_slots)
            self.ax_vol.set_xlim(-0.8, min_slots)

        step = max(1, n_candles // 10)
        tick_locs = list(range(0, n_candles, step))
        if (n_candles - 1) not in tick_locs:
            tick_locs.append(n_candles - 1)
        tick_lbls = [timestamps[idx] if idx < len(timestamps) else "" for idx in tick_locs]

        self.ax_vol.set_xticks(tick_locs)
        self.ax_vol.set_xticklabels(tick_lbls, rotation=0, color=TEXT_MUTED, fontsize=8)
        self.ax_main.tick_params(labelbottom=False)

        # Update Top Info Bar Badge
        chg_pct = m_item.get("change_pct", 0.0) if m_item else 0.0
        chg_str = f"+{chg_pct:.2f}%" if chg_pct > 0 else f"{chg_pct:.2f}%"
        zone_str = m_item.get("zone", "Active") if m_item else "Active"
        cpr_w = pivots.cpr_width_pct if pivots else (m_item.get("cpr_width_pct", 0.0) if m_item else 0.0)
        cpr_type = "⚡ Narrow CPR" if (pivots and pivots.is_narrow_cpr) else "Standard CPR"

        info_text = f"{sym}  |  LTP: ₹{ltp:.2f} ({chg_str})  |  {cpr_type} ({cpr_w:.2f}%)  |  {zone_str}"
        self.info_lbl.config(text=info_text)

        try:
            self.fig.subplots_adjust(left=0.06, right=0.88, top=0.97, bottom=0.07, hspace=0.05)
        except Exception:
            pass
        self.canvas.draw_idle()
