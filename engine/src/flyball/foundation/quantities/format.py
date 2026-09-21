def format_quantity(flow: float, units: str | None = None) -> str:
    return f"{flow:.3f}{f' {units}' if units else ''}"


__all__ = ["format_quantity"]
