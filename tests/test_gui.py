import tkinter as tk
import pytest
from gui.app import ScannerTkinterGUI
from web.state import dashboard_state
from indicators.pivots import calculate_daily_pivots

@pytest.fixture
def tk_root():
    root = tk.Tk()
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:
        pass

def test_tkinter_gui_initialization(tk_root):
    # Seed state with mock pivot and signal
    piv = calculate_daily_pivots(
        symbol="RELIANCE",
        date_str="2026-09-01",
        open_p=1280.0,
        high_p=1300.0,
        low_p=1260.0,
        close_p=1280.0,
        volume=5000000,
    )
    pivots = {"RELIANCE": piv}
    dashboard_state.initialize_pivots(pivots)
    dashboard_state.add_signal({
        "timestamp": "2026-09-01T10:15:00",
        "symbol": "RELIANCE",
        "direction": "BULLISH SETUP",
        "pattern": "BULLISH ENGULFING",
        "price": 1285.0,
        "score": 8,
        "zone": "Inside Narrow CPR",
        "pp": 1280.0,
        "pdh": 1300.0,
        "pdl": 1260.0,
        "r1": 1300.0,
        "s1": 1260.0,
        "relative_volume": 2.5,
        "conditions_met": ["⚡ Narrow CPR (<0.1%)", "Price > Pivot"],
    })

    gui = ScannerTkinterGUI(tk_root)
    assert gui.card_symbols_val.get() == "1"
    assert len(gui.cached_signals) >= 1
    assert len(gui.cached_market) >= 1

    # Verify signals tree has item
    items = gui.signals_tree.get_children()
    assert len(items) >= 1

    # Verify market tree has item
    m_items = gui.market_tree.get_children()
    assert len(m_items) >= 1

    # Verify MultiSelectDropdown
    assert hasattr(gui, "signal_cpr_menu")
    assert hasattr(gui, "market_cpr_menu")
    assert gui.signal_cpr_menu.is_all_selected() is True

    # Deselect all and select one option
    gui.signal_cpr_menu.all_var.set(False)
    gui.signal_cpr_menu._on_toggle_all()
    assert gui.signal_cpr_menu.is_all_selected() is True  # 0 selected treated as ALL or none

    gui.signal_cpr_menu.vars["⚡ Narrow CPR (<= 0.2%)"].set(True)
    gui.signal_cpr_menu._on_toggle_option()
    assert gui.signal_cpr_menu.is_all_selected() is False
    assert "⚡ Narrow CPR (<= 0.2%)" in gui.signal_cpr_menu.get_selected()

    # Verify Tab 3 (Candlestick Chart & CPR Levels)
    assert hasattr(gui, "tab_chart")
    assert hasattr(gui, "chart_frame")
    assert gui.chart_frame.current_symbol == "RELIANCE"

    # Test switching symbol and opening chart
    gui.open_chart_for_symbol("RELIANCE")
    assert gui.chart_frame.current_symbol == "RELIANCE"
    gui.chart_frame.populate_symbols(["RELIANCE", "TATAELXSI", "INFY"])
    assert "TATAELXSI" in gui.chart_frame.symbol_combo["values"]

    # Test chart redraw execution
    gui.chart_frame.redraw_chart()
    assert gui.chart_frame.ax_main is not None
    assert gui.chart_frame.ax_vol is not None

    # Test zoom, pan, and auto-fit controls
    gui.chart_frame.zoom_in()
    assert gui.chart_frame._custom_xlim is not None
    gui.chart_frame.zoom_out()
    gui.chart_frame.pan_left()
    gui.chart_frame.pan_right()
    gui.chart_frame.reset_view()
    assert gui.chart_frame._custom_xlim is None

    # Test auto-fit toggle
    gui.chart_frame.auto_fit.set(False)
    gui.chart_frame._on_autofit_toggle()
    assert gui.chart_frame.auto_fit.get() is False
    gui.chart_frame.auto_fit.set(True)
    gui.chart_frame._on_autofit_toggle()
    assert gui.chart_frame.auto_fit.get() is True
