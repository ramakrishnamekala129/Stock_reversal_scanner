from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_python_source_does_not_reintroduce_sub_five_minute_candles():
    one_minute_suffix = "1" + "m"
    forbidden = (
        "1" + "minute",
        "1" + "-minute",
        "candles_history_" + one_minute_suffix,
        "raw_" + one_minute_suffix,
        "df_" + one_minute_suffix,
    )
    violations: list[str] = []

    for source_path in PROJECT_ROOT.rglob("*.py"):
        if any(
            part in {".git", ".kilo", ".venv", "venv", "__pycache__"}
            for part in source_path.parts
        ):
            continue
        source = source_path.read_text(encoding="utf-8", errors="ignore").lower()
        if any(token in source for token in forbidden):
            violations.append(str(source_path.relative_to(PROJECT_ROOT)))

    assert not violations, f"Sub-5-minute candle references found in: {violations}"
