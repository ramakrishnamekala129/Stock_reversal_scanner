"""
Console & Terminal Output Formatter Module.
Produces clean, readable ASCII signal cards and startup banners.
"""

from datetime import datetime
from scanner.signal_engine import SignalEvent


class ConsoleFormatter:
    """Formats scanner events and banners for terminal output."""

    @staticmethod
    def print_startup_banner(
        rest_status: str,
        analytics_status: str,
        ws_status: str,
        fno_count: int,
        hist_status: str = "LOADED",
        pivot_status: str = "READY",
    ):
        """Prints application startup summary banner."""
        print("\n" + "=" * 60)
        print("UPSTOX 5-MINUTE F&O INTRADAY SCANNER")
        print("=" * 60)
        print(f"Upstox REST       : {rest_status}")
        print(f"Analytics Token   : {analytics_status}")
        print(f"WebSocket         : {ws_status}")
        print("")
        print(f"F&O Instruments   : {fno_count}")
        print(f"Historical Data   : {hist_status}")
        print(f"Pivot Levels      : {pivot_status}")
        print(f"5M Scanner        : RUNNING")
        print("=" * 60 + "\n")

    @staticmethod
    def format_signal(signal: SignalEvent) -> str:
        """
        Formats a SignalEvent into the exact console structure requested.
        """
        ts = signal.timestamp
        if isinstance(ts, datetime):
            time_str = ts.strftime("%H:%M:%S")
        else:
            time_str = str(ts).split("T")[-1].split("+")[0]

        lines = [
            "=" * 60,
            "5-MINUTE F&O SCANNER",
            "=" * 60,
            "",
            f"Time: {time_str}",
            "",
            f"Symbol       {signal.symbol}",
            f"Pattern      {signal.pattern}",
            f"Price        {signal.price:.2f}",
            "",
            f"PDH          {signal.pdh:.2f}",
            f"PDL          {signal.pdl:.2f}",
            f"PDC          {signal.pdc:.2f}",
            "",
            f"Pivot        {signal.pivot:.2f}",
            f"R1           {signal.r1:.2f}",
            f"R2           {signal.r2:.2f}",
            f"R3           {signal.r3:.2f}",
            "",
        ]

        if signal.conditions_met:
            for cond in signal.conditions_met:
                lines.append(f"{cond}")
            lines.append("")

        lines.append(f"Relative Volume: {signal.relative_volume:.2f}")
        lines.append("")
        lines.append(f"Signal Score: {signal.score}")
        lines.append(f"Signal: {signal.direction}")
        lines.append("=" * 60)

        return "\n".join(lines)

    @staticmethod
    def print_signal(signal: SignalEvent):
        """Directly prints a formatted signal card to stdout."""
        card = ConsoleFormatter.format_signal(signal)
        print("\n" + card + "\n")
