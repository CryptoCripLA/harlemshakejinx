"""Quarter-Kelly position sizing (CHAD, matrix row C13).

CHAD, verbatim: "Per-user, per-score-bucket Bayesian model"; "Quarter-Kelly default — conservative by design"; "After
20+ closed trades per score bucket, Kelly updates its win-rate and win/loss-ratio estimates from your actual results";
"Change your min score threshold and Kelly resets to conservative sizing — your old priors no longer apply to the new
population"; "Cold-start uses conservative bootstrap priors until 20+ trades per bucket"; "Kelly sizes up to the cap".

What is CHAD's and fixed here: the quarter fraction (0.25), the 20-trade switch, per-bucket models, the reset on an
entry-filter change, the Kelly formula f* = p - (1 - p) / b. What CHAD does not give (B-03) and is therefore required
configuration, refused when missing: the bucket edges, the bootstrap prior (win rate, payout ratio) of each bucket,
the cap, and which bucket an unscored (fast-path) entry uses (B-31). CHAD names a "Bayesian model" but specifies only
the prior-until-20-trades switch, so that switch is what is implemented; no prior strength or update rule is invented.

Measures: a trade wins when its realized P&L after every cost is positive; returns are P&L / cost; the payout ratio b
is the mean winning return over the mean losing return (absolute). JINX has one user (the owner). Paper and live
results train separate models. The bankroll is the actual paper/live balance minus pending entry reservations and the
network fees of one buy and one sell.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal

from jinx.ledger import PositionLedger, Status
from jinx.paper import PaperWallet
from jinx.store import Store

QUARTER = Decimal("0.25")             # "Quarter-Kelly default"
MIN_CLOSED_TRADES = 20                # "After 20+ closed trades per score bucket"
SETTING_KEY = "sizing.kelly"
LABELS = ("OWNER_CONFIG",)
TEST_FIXTURE_PREFIX = "TEST_FIXTURE"


def kelly_fraction(win_rate: Decimal, payout_ratio: Decimal) -> Decimal:
    """Quarter of f* = p - (1 - p) / b."""
    return QUARTER * (win_rate - (1 - win_rate) / payout_ratio)


@dataclass(frozen=True)
class KellyConfig:
    label: str
    bucket_floors: tuple[Decimal, ...]                       # ascending; a score belongs to the highest floor <= it
    priors: dict[str, tuple[Decimal, Decimal]]               # bucket floor (text) -> (win rate, payout ratio)
    cap_fraction: Decimal                                    # largest fraction of the bankroll one entry may use
    entry_filter: str                                        # the entry filter in force; a change resets the model
    unscored_bucket: Decimal | None = None                   # the bucket for entries without a score (B-31)

    def __post_init__(self) -> None:
        if self.label not in LABELS and not self.label.startswith(TEST_FIXTURE_PREFIX):
            raise ValueError(f"label must be one of {LABELS} or start with {TEST_FIXTURE_PREFIX}")
        floors = list(self.bucket_floors)
        if not floors or floors != sorted(set(floors)) or not all(isinstance(f, Decimal) for f in floors):
            raise ValueError("bucket_floors must be distinct ascending Decimals")
        if set(self.priors) != {str(f) for f in floors}:
            raise ValueError("every bucket needs exactly one prior")
        for p, b in self.priors.values():
            if not (Decimal(0) <= p <= Decimal(1)) or not b > 0:
                raise ValueError("a prior needs 0 <= win rate <= 1 and payout ratio > 0")
        if not (Decimal(0) < self.cap_fraction <= Decimal(1)):
            raise ValueError("cap_fraction must be in (0, 1]")
        if self.unscored_bucket is not None and self.unscored_bucket not in floors:
            raise ValueError("unscored_bucket must be one of the bucket floors")
        if not self.entry_filter:
            raise ValueError("entry_filter is required")

    def bucket_for(self, score: Decimal | None) -> Decimal | None:
        if score is None:
            return self.unscored_bucket
        below = [f for f in self.bucket_floors if f <= score]
        return below[-1] if below else None

    def to_dict(self) -> dict:
        return {"label": self.label, "bucket_floors": [str(f) for f in self.bucket_floors],
                "priors": {k: [str(p), str(b)] for k, (p, b) in self.priors.items()},
                "cap_fraction": str(self.cap_fraction), "entry_filter": self.entry_filter,
                "unscored_bucket": None if self.unscored_bucket is None else str(self.unscored_bucket)}

    @classmethod
    def from_dict(cls, d: dict) -> "KellyConfig":
        return cls(d["label"], tuple(Decimal(f) for f in d["bucket_floors"]),
                   {k: (Decimal(p), Decimal(b)) for k, (p, b) in d["priors"].items()}, Decimal(d["cap_fraction"]),
                   d["entry_filter"], None if d.get("unscored_bucket") is None else Decimal(d["unscored_bucket"]))


def save_kelly_config(store: Store, cfg: KellyConfig, by: str) -> None:
    store.set_setting(SETTING_KEY, json.dumps(cfg.to_dict(), sort_keys=True), by=by)


def load_kelly_config(store: Store) -> KellyConfig | None:
    raw = store.get_setting(SETTING_KEY)
    if raw is None:
        return None
    try:
        return KellyConfig.from_dict(json.loads(raw))
    except (ValueError, TypeError, KeyError, ArithmeticError):
        return None                                           # unreadable: fail closed


@dataclass(frozen=True)
class SizeDecision:
    ok: bool
    budget_lamports: int
    reason: str | None
    detail: dict = field(default_factory=dict)


class QuarterKelly:
    def __init__(self, ledger: PositionLedger, wallet: PaperWallet, config: KellyConfig | None,
                 network_fee_lamports: int) -> None:
        self.ledger, self.wallet, self.config, self.network_fee = ledger, wallet, config, network_fee_lamports

    def _history(self, cfg: KellyConfig, bucket: Decimal, simulated: bool) -> list[Decimal]:
        """Returns (P&L / cost) of this bucket's closed trades under the current entry filter."""
        rows = self.ledger.store.conn.execute(
            "SELECT entry_score, cost_lamports, realized_pnl_lamports FROM positions WHERE status = ? AND simulated = ?"
            " AND entry_filter = ? AND realized_pnl_lamports IS NOT NULL AND cost_lamports > 0",
            (Status.CLOSED.value, int(simulated), cfg.entry_filter)).fetchall()
        out = []
        for score, cost, pnl in rows:
            if cfg.bucket_for(None if score is None else Decimal(score)) == bucket:
                out.append(Decimal(pnl) / Decimal(cost))
        return out

    def size(self, entry_score: Decimal | None, simulated: bool = True) -> SizeDecision:
        cfg = self.config
        if cfg is None:
            return SizeDecision(False, 0, "SIZING_NOT_CONFIGURED", {"blocker": "B-03"})
        bucket = cfg.bucket_for(entry_score)
        if bucket is None:
            return SizeDecision(False, 0, "NO_SCORE_BUCKET" if entry_score is None else "BELOW_LOWEST_BUCKET",
                                {"entry_score": None if entry_score is None else str(entry_score)})
        returns = self._history(cfg, bucket, simulated)
        detail = {"label": cfg.label, "bucket": str(bucket), "closed_trades": len(returns),
                  "entry_filter": cfg.entry_filter, "simulated": simulated, "user": "owner",
                  "entry_score": None if entry_score is None else str(entry_score)}
        if len(returns) >= MIN_CLOSED_TRADES:
            wins = [r for r in returns if r > 0]
            losses = [-r for r in returns if r <= 0]
            p = Decimal(len(wins)) / Decimal(len(returns))
            detail["source"] = "calibrated"
            if not wins:
                return SizeDecision(False, 0, "NO_EDGE", {**detail, "win_rate": str(p)})
            if not losses or sum(losses) == 0:
                b, f_star = None, p                            # f* -> p as the payout ratio grows without bound
            else:
                b = (sum(wins) / len(wins)) / (sum(losses) / len(losses))
                f_star = p - (1 - p) / b
            f = QUARTER * f_star
        else:
            p, b = cfg.priors[str(bucket)]
            detail["source"] = "prior"
            f = kelly_fraction(p, b)
        detail.update(win_rate=str(p), payout_ratio=None if b is None else str(b), quarter_kelly=str(f))
        if f <= 0:
            return SizeDecision(False, 0, "NO_EDGE", detail)
        used = min(f, cfg.cap_fraction)
        committed = sum(pos.entry_budget_lamports or 0 for pos in self.ledger.active()
                        if pos.status is Status.PENDING_OPEN)
        bankroll = self.wallet.balance() - committed - 2 * self.network_fee
        budget = int(Decimal(max(bankroll, 0)) * used)
        detail.update(fraction_used=str(used), cap_fraction=str(cfg.cap_fraction), bankroll_lamports=bankroll,
                      committed_lamports=committed)
        if budget <= 0:
            return SizeDecision(False, 0, "INSUFFICIENT_BALANCE", detail)
        return SizeDecision(True, budget, None, detail)
