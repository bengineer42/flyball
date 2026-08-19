

def validate_normalised(name: str, value: float) -> float:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0.0 and 1.0, got {value}")
    return value


def format_quantity(flow: float, units: str | None = None) -> str:
    return f"{flow:.3f}{f' {units}' if units else ''}"
