from decimal import ROUND_HALF_UP, Decimal

_CONFIDENCE_LABELS = {"high": "高", "medium": "中", "low": "低"}


def _finite_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        d = Decimal(str(value))
        if not d.is_finite():
            return None
        return d
    except (ValueError, ArithmeticError):
        return None


def _format_wan_number(amount: Decimal) -> str:
    wan = amount / Decimal("10000")
    quantized = wan.quantize(Decimal("0.0"), rounding=ROUND_HALF_UP)
    if quantized == quantized.to_integral_value():
        formatted = str(quantized.quantize(Decimal("1.")))
    else:
        formatted = str(quantized)
    parts = formatted.split(".")
    int_part = parts[0]
    sign = ""
    if int_part.startswith("-"):
        sign = "-"
        int_part = int_part[1:]
    groups = []
    while len(int_part) > 3:
        groups.append(int_part[-3:])
        int_part = int_part[:-3]
    groups.append(int_part)
    result = sign + ",".join(reversed(groups))
    if len(parts) > 1:
        result += "." + parts[1]
    return result


def format_total_price_wan(value: object) -> str:
    amount = _finite_decimal(value)
    if amount is None or amount <= 0:
        return "—"
    return f"{_format_wan_number(amount)} 萬"


def format_unit_price_wan(value: object) -> str:
    amount = _finite_decimal(value)
    if amount is None or amount <= 0:
        return "—"
    return f"{_format_wan_number(amount)} 萬／坪"


def localize_confidence(value: object) -> str:
    if value is None:
        return "—"
    return _CONFIDENCE_LABELS.get(value, "—")
