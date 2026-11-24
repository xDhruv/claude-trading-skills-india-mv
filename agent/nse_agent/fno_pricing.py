"""Black-Scholes price and Greeks. Shared by the verdict logic and the strategy library."""
import math

RATE = 0.065


def _cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _pdf(x: float) -> float:
    return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def bs_price(kind: str, s: float, k: float, t: float, iv_pct: float, r: float = RATE) -> float:
    sig = iv_pct / 100
    if t <= 0 or sig <= 0:
        return max(s - k, 0.0) if kind == "C" else max(k - s, 0.0)
    d1 = (math.log(s / k) + (r + sig * sig / 2) * t) / (sig * math.sqrt(t))
    d2 = d1 - sig * math.sqrt(t)
    if kind == "C":
        return s * _cdf(d1) - k * math.exp(-r * t) * _cdf(d2)
    return k * math.exp(-r * t) * _cdf(-d2) - s * _cdf(-d1)


def greeks(kind: str, s: float, k: float, t: float, iv_pct: float, r: float = RATE) -> dict | None:
    sig = iv_pct / 100
    if t <= 0 or sig <= 0:
        return None
    d1 = (math.log(s / k) + (r + sig * sig / 2) * t) / (sig * math.sqrt(t))
    d2 = d1 - sig * math.sqrt(t)
    delta = _cdf(d1) if kind == "C" else _cdf(d1) - 1
    decay = -(s * _pdf(d1) * sig) / (2 * math.sqrt(t))
    carry = -r * k * math.exp(-r * t) * (_cdf(d2) if kind == "C" else -_cdf(-d2))
    return {"delta": delta, "gamma": _pdf(d1) / (s * sig * math.sqrt(t)),
            "theta_day": (decay + carry) / 365, "vega_pt": s * _pdf(d1) * math.sqrt(t) / 100}
