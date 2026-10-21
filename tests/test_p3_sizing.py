"""CHAD Kelly sizing (matrix C13): "Per-user, per-score-bucket Bayesian model"; "Quarter-Kelly default"; "After 20+
closed trades per score bucket, Kelly updates its win-rate and win/loss-ratio estimates from your actual results";
"Change your min score threshold and Kelly resets to conservative sizing"; "Cold-start uses conservative bootstrap
priors until 20+ trades per bucket". Bucket edges, priors and the cap are not in CHAD (B-03): they are required
configuration, refused when missing, and the values below are labelled test fixtures.
"""
from decimal import Decimal

import pytest

from jinx.ledger import Fill, PositionLedger, Status
from jinx.core.events import Side
from jinx.modes import Mode, set_mode
from jinx.paper import PaperWallet
from jinx.sizing.kelly import KellyConfig, QuarterKelly, kelly_fraction, load_kelly_config, save_kelly_config

FIX = KellyConfig(label="TEST_FIXTURE_kelly", bucket_floors=(Decimal(60), Decimal(70), Decimal(80)),
                  priors={"60": (Decimal("0.34"), Decimal("1.5")), "70": (Decimal("0.40"), Decimal("1.5")),
                          "80": (Decimal("0.48"), Decimal("1.5"))},
                  cap_fraction=Decimal("0.05"), entry_filter="min_score=60", unscored_bucket=None)


def _rig(store, start=2_000_000_000):
    ledger = PositionLedger(store)
    wallet = PaperWallet(store, start)
    set_mode(store, Mode.AUTO)
    return ledger, wallet


def _closed_trade(ledger, n, score, pnl, cost=100_000_000, entry_filter="min_score=60", simulated=True):
    r = ledger.reserve_entry(f"t{n}", f"M{n}", cost, simulated=simulated,
                             fields={"entry_score": str(score), "entry_filter": entry_filter, "strategy": "AUTO"})
    pid = r.position_id
    ledger.record_buy_fill(pid, Fill(f"b{n}", Side.BUY, 1_000, cost, 0, 0, simulated, 1))
    ledger.claim_exit(f"x{n}", pid, None, f"s{n}")
    ledger.record_sell_fill(pid, Fill(f"s{n}", Side.SELL, 1_000, cost + pnl, 0, 0, simulated, 2))
    assert ledger.get(pid).status is Status.CLOSED


def test_quarter_kelly_formula():
    # f* = p - (1 - p) / b ; quarter-Kelly = f* / 4
    assert kelly_fraction(Decimal("0.5"), Decimal("2")) == Decimal("0.25") / 4 * 1
    assert kelly_fraction(Decimal("0.4"), Decimal("1.5")) == (Decimal("0.4") - Decimal("0.6") / Decimal("1.5")) / 4
    assert kelly_fraction(Decimal("0.3"), Decimal("1")) < 0                     # no edge: no bet


def test_unconfigured_sizing_refuses_every_entry(store):
    ledger, wallet = _rig(store)
    d = QuarterKelly(ledger, wallet, None, 5_000).size(Decimal(70))
    assert not d.ok and d.reason == "SIZING_NOT_CONFIGURED"


def test_cold_start_uses_the_configured_prior_and_the_cap(store):
    ledger, wallet = _rig(store)
    d = QuarterKelly(ledger, wallet, FIX, 5_000).size(Decimal(72))
    assert kelly_fraction(Decimal("0.40"), Decimal("1.5")) == 0                 # 0.40 - 0.60/1.5 = 0: no edge
    assert not d.ok and d.reason == "NO_EDGE" and d.detail["bucket"] == "70" and d.detail["source"] == "prior"
    d = QuarterKelly(ledger, wallet, FIX, 5_000).size(Decimal(85))
    f = kelly_fraction(Decimal("0.48"), Decimal("1.5"))
    bankroll = 2_000_000_000 - 2 * 5_000
    assert d.ok and d.budget_lamports == int(bankroll * min(f, Decimal("0.05")))
    assert d.detail["fraction_used"] == str(min(f, Decimal("0.05")))


def test_no_edge_means_no_trade(store):
    ledger, wallet = _rig(store)
    d = QuarterKelly(ledger, wallet, FIX, 5_000).size(Decimal(65))             # prior 0.34 / 1.5: negative
    assert not d.ok and d.reason == "NO_EDGE"


def test_scores_outside_the_buckets_and_unscored_entries_are_refused(store):
    ledger, wallet = _rig(store)
    q = QuarterKelly(ledger, wallet, FIX, 5_000)
    assert q.size(Decimal(59)).reason == "BELOW_LOWEST_BUCKET"
    assert q.size(None).reason == "NO_SCORE_BUCKET"


def test_recalibrates_from_actual_results_after_20_closed_trades_in_the_bucket(store):
    ledger, wallet = _rig(store, start=20_000_000_000)
    for i in range(19):                                                         # 19: still the prior
        _closed_trade(ledger, i, 82, 100_000_000 if i % 2 == 0 else -50_000_000)
    q = QuarterKelly(ledger, wallet, FIX, 5_000)
    assert q.size(Decimal(81)).detail["source"] == "prior"
    _closed_trade(ledger, 19, 82, -50_000_000)                                  # the 20th closed trade
    d = q.size(Decimal(81))
    assert d.detail["source"] == "calibrated" and d.detail["closed_trades"] == 20
    assert Decimal(d.detail["win_rate"]) == Decimal(10) / 20
    assert Decimal(d.detail["payout_ratio"]) == Decimal(2)                      # +100 % wins / -50 % losses
    # other buckets keep their own model
    assert q.size(Decimal(75)).detail["source"] == "prior"


def test_changing_the_entry_filter_resets_to_the_prior(store):
    ledger, wallet = _rig(store, start=20_000_000_000)
    for i in range(25):
        _closed_trade(ledger, i, 82, 100_000_000 if i % 2 == 0 else -50_000_000)
    assert QuarterKelly(ledger, wallet, FIX, 5_000).size(Decimal(81)).detail["source"] == "calibrated"
    changed = KellyConfig(**{**FIX.__dict__, "entry_filter": "min_score=65"})
    d = QuarterKelly(ledger, wallet, changed, 5_000).size(Decimal(81))
    assert d.detail["source"] == "prior" and d.detail["closed_trades"] == 0


def test_paper_and_live_results_train_separate_models(store):
    ledger, wallet = _rig(store, start=20_000_000_000)
    for i in range(25):
        _closed_trade(ledger, i, 82, 100_000_000, simulated=False)
    d = QuarterKelly(ledger, wallet, FIX, 5_000).size(Decimal(81), simulated=True)
    assert d.detail["source"] == "prior"


def test_size_reads_the_actual_balance_and_pending_commitments(store):
    ledger, wallet = _rig(store, start=1_000_000_000)
    ledger.reserve_entry("pending", "PENDINGMINT", 300_000_000, simulated=True)   # reserved, not yet filled
    d = QuarterKelly(ledger, wallet, FIX, 5_000).size(Decimal(85))
    assert d.detail["bankroll_lamports"] == 1_000_000_000 - 300_000_000 - 2 * 5_000


def test_config_is_labelled_and_complete(store):
    with pytest.raises(ValueError):
        KellyConfig(**{**FIX.__dict__, "label": "default"})
    with pytest.raises(ValueError):
        KellyConfig(**{**FIX.__dict__, "priors": {"60": FIX.priors["60"]}})       # a bucket without a prior
    with pytest.raises(ValueError):
        KellyConfig(**{**FIX.__dict__, "cap_fraction": Decimal("1.5")})
    assert load_kelly_config(store) is None
    save_kelly_config(store, FIX, by="test")
    assert load_kelly_config(store) == FIX
