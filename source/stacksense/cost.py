"""Monthly cost model. Each KB entry carries a cost model; this module evaluates it at the project's load.

Model keys (all optional, USD per month, [low, high] pairs):
  fixed            flat price
  curve            [[peak_rps, low, high], ...] instance/cluster cost vs load, log-log interpolated
  per_seat         price per engineer seat
  per_gb_storage   per GB of data stored
  per_gb_egress    per GB sent out beyond free_egress_gb
  per_m_requests   per million requests beyond free_requests_m
  per_k_users      per thousand users beyond free_users
  ha_factor        multiplier on fixed+curve when the availability target needs redundancy
Usage-driven items are widened (x0.75 low, x1.3 high) because load estimates are order-of-magnitude."""
import math

USAGE_LOW, USAGE_HIGH = 0.75, 1.3
SPREAD = 0.45


def _interp(curve, x):
    xs = [max(c[0], 1e-9) for c in curve]
    x = max(x, 1e-9)
    if len(curve) == 1:
        return curve[0][1], curve[0][2]
    if x <= xs[0]:
        return curve[0][1], curve[0][2]
    for i in range(1, len(curve)):
        if x <= xs[i]:
            t = (math.log(x) - math.log(xs[i - 1])) / (math.log(xs[i]) - math.log(xs[i - 1]))
            return tuple(_geo(curve[i - 1][k], curve[i][k], t) for k in (1, 2))
    t = (math.log(x) - math.log(xs[-2])) / (math.log(xs[-1]) - math.log(xs[-2]))
    return tuple(_geo(curve[-2][k], curve[-1][k], t) for k in (1, 2))


def _geo(a, b, t):
    if a <= 0 or b <= 0:
        return a + (b - a) * t
    return math.exp(math.log(a) + (math.log(b) - math.log(a)) * t)


def _tighten(lo, hi):
    """Keep the central part of a price range (in log space): KB pairs span list-price extremes, the band shows the likely part."""
    if hi <= 0:
        return 0.0, 0.0
    if lo <= 0:
        return 0.0, hi * 0.85
    c = math.sqrt(lo * hi)
    return c * (lo / c) ** SPREAD, c * (hi / c) ** SPREAD


def evaluate(model, p, ha=False):
    """Return (low, high) monthly USD for one cost model at profile p."""
    if not model:
        return 0.0, 0.0
    flo, fhi = model.get("fixed", [0, 0])
    ulo = uhi = 0.0
    if model.get("curve"):
        clo, chi = _interp(model["curve"], p["peak_rps"])
        ulo, uhi = ulo + clo, uhi + chi
    if ha and model.get("ha_factor", 1.0) > 1.0:
        f = model["ha_factor"]
        flo, fhi, ulo, uhi = flo * f, fhi * f, ulo * f, uhi * f
    if "per_seat" in model:
        seats = max(0, p["team_size"] - model.get("free_seats", 0))
        flo += model["per_seat"][0] * seats
        fhi += model["per_seat"][1] * seats
    if "per_gb_storage" in model:
        ulo += model["per_gb_storage"][0] * p["data_gb"]
        uhi += model["per_gb_storage"][1] * p["data_gb"]
    if "per_gb_egress" in model:
        gb = max(0.0, p["egress_gb"] * model.get("egress_share", 1.0) - model.get("free_egress_gb", 0))
        ulo += model["per_gb_egress"][0] * gb
        uhi += model["per_gb_egress"][1] * gb
    if "per_m_requests" in model:
        m = max(0.0, p["monthly_requests"] / 1e6 - model.get("free_requests_m", 0))
        ulo += model["per_m_requests"][0] * m
        uhi += model["per_m_requests"][1] * m
    if "per_k_users" in model:
        k = max(0.0, (p["users_12m"] - model.get("free_users", 0)) / 1000.0)
        ulo += model["per_k_users"][0] * k
        uhi += model["per_k_users"][1] * k
    return _tighten(flo + USAGE_LOW * ulo, fhi + USAGE_HIGH * uhi)


def needs_ha(p):
    return p["avail_level"] >= 3.0


def component_cost(comp, p, mode=None):
    """Cost of a core component. A database running on your own host costs nothing extra unless it is licensed."""
    if mode == "self_hosted" and not comp.get("attrs", {}).get("licensed"):
        return 0.0, 0.0
    return evaluate(comp.get("cost"), p, ha=needs_ha(p))


def fmt_money(x):
    if x < 1:
        return "$0"
    if x < 100:
        return "$%d" % round(x)
    return "$%s" % format(int(round(x, -1 if x < 1000 else -2)), ",")


def line(label, lo, hi, note=""):
    span = fmt_money(lo) if round(lo) == round(hi) else "%s-%s" % (fmt_money(lo), fmt_money(hi))
    return "%s: about %s/month%s" % (label, span, (" (%s)" % note) if note else "")
