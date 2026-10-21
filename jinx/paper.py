"""Paper wallet and paper execution.

Paper fills are priced on the pump.fun bonding curve from the latest reserves
JINX observed for the token, using D3AD's curve math translated line by line
(Apache-2.0, pinned in UPSTREAM_PINS.json). Every paper fill is marked
simulated. A paper fill is the price the curve would have charged at the last
observed state; it does not model latency, competing buys or slippage. Those
belong to Phase 3.

Known limitation: the PumpPortal feed carries only virtual reserves, not the
curve's real token reserves, so the real-reserve cap in D3AD's
`initial_buy_price` is not applied here (it only matters near graduation).

Portions translated from D3AD-E/Solana-sniper-bot @ a91ca336 (Apache License 2.0;
license in third_party/d3ad/LICENSE; see THIRD_PARTY_NOTICES.md). Changes from
the original: translated from Rust to Python; `initial_buy_price` applied to the
current reserves instead of the curve's initial reserves; the real-token cap
made optional; `sell_proceeds` also returns the fee.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from jinx.core.events import Side
from jinx.ledger import Fill, FillConflict, PositionLedger
from jinx.registry import TokenRegistry
from jinx.store import Store

BPS = 10_000


@dataclass(frozen=True)
class CurveFees:
    """pump.fun fee rates from its global config (protocol fee + creator fee)."""
    fee_bps: int
    creator_fee_bps: int

    @property
    def total_bps(self) -> int:
        # SRC: d3ad@a91ca336 shred-sniper/sniper/src/pumpfun.rs:432-434 (total_fee_bps)
        return self.fee_bps + self.creator_fee_bps


def curve_tokens_out(v_sol: int, v_tokens: int, curve_sol: int, real_token_cap: int | None = None) -> int:
    """Tokens received for `curve_sol` lamports of curve volume at reserves (v_sol, v_tokens)."""
    # SRC: d3ad@a91ca336 shred-sniper/sniper/src/pumpfun.rs:439-449 (initial_buy_price),
    # applied to the current reserves instead of the initial ones.
    if curve_sol == 0:
        return 0
    n = v_sol * v_tokens
    i = v_sol + curve_sol
    r = n // i + 1
    s = max(v_tokens - r, 0)
    return s if real_token_cap is None else min(s, real_token_cap)


def curve_sol_for_budget(budget_lamports: int, total_fee_bps: int) -> int:
    """Curve volume a budget buys once the fee is backed out (the budget covers curve cost plus fees)."""
    # SRC: d3ad@a91ca336 shred-sniper/sniper/src/pumpfun.rs:485-486 (plan_buy: curve_sol)
    return budget_lamports * BPS // (BPS + total_fee_bps)


def sell_proceeds(v_sol: int, v_tokens: int, amount: int, total_fee_bps: int) -> tuple[int, int]:
    """(net lamports out, fee lamports) for selling `amount` tokens into the curve."""
    # SRC: d3ad@a91ca336 shred-sniper/sniper/src/position.rs:631-646 (sell_proceeds)
    if amount == 0 or v_tokens == 0 or v_sol == 0:
        return 0, 0
    k = v_sol * v_tokens
    new_token = v_tokens + amount
    new_sol = k // new_token
    gross = max(v_sol - new_sol, 0)
    fee = gross * total_fee_bps // BPS
    return max(gross - fee, 0), fee


def buy_fill_id(position_id: int, intent_id: str) -> str:
    return f"p{position_id}:{intent_id}:buy"


def sell_fill_id(position_id: int, intent_id: str) -> str:
    return f"p{position_id}:{intent_id}:sell"


class InsufficientBalance(Exception):
    pass


@dataclass(frozen=True)
class Reconciliation:
    ok: bool
    starting_lamports: int
    entries_lamports: int
    balance_lamports: int


class PaperWallet:
    """Simulated SOL balance. Every change is an idempotent ledger entry, so the
    balance always equals the starting amount plus the sum of its entries."""

    def __init__(self, store: Store, starting_lamports: int) -> None:
        self.store = store
        with store.transaction() as c:
            c.execute("INSERT OR IGNORE INTO paper_wallet (id, starting_lamports, balance_lamports) VALUES (1, ?, ?)",
                      (starting_lamports, starting_lamports))

    def balance(self) -> int:
        return self.store.conn.execute("SELECT balance_lamports FROM paper_wallet WHERE id = 1").fetchone()[0]

    def apply(self, entry_id: str, delta_lamports: int, reason: str, ts_ns: int | None = None) -> bool:
        with self.store.transaction() as c:
            if c.execute("SELECT 1 FROM paper_wallet_entries WHERE entry_id = ?", (entry_id,)).fetchone():
                return False
            if self.balance() + delta_lamports < 0:
                raise InsufficientBalance(f"{entry_id}: balance {self.balance()} + {delta_lamports} < 0")
            c.execute("INSERT INTO paper_wallet_entries (entry_id, delta_lamports, reason, ts_ns) VALUES (?,?,?,?)",
                      (entry_id, delta_lamports, reason, ts_ns or time.time_ns()))
            c.execute("UPDATE paper_wallet SET balance_lamports = balance_lamports + ? WHERE id = 1",
                      (delta_lamports,))
            return True

    def reconcile(self) -> Reconciliation:
        start, bal = self.store.conn.execute(
            "SELECT starting_lamports, balance_lamports FROM paper_wallet WHERE id = 1").fetchone()
        total = self.store.conn.execute("SELECT COALESCE(SUM(delta_lamports), 0) FROM paper_wallet_entries").fetchone()[0]
        return Reconciliation(start + total == bal, start, total, bal)


@dataclass(frozen=True)
class ExecResult:
    ok: bool
    position_id: int | None
    reason: str | None = None


class PaperExecutor:
    def __init__(self, store: Store, ledger: PositionLedger, wallet: PaperWallet, registry: TokenRegistry,
                 fees: CurveFees, network_fee_lamports: int, clock_ns=time.time_ns) -> None:
        self.store, self.ledger, self.wallet, self.registry = store, ledger, wallet, registry
        self.fees = fees
        self.network_fee = network_fee_lamports
        self.clock_ns = clock_ns

    def buy(self, position_id: int, intent_id: str, mint: str, budget_lamports: int) -> ExecResult:
        tok = self.registry.get(mint)
        if tok is None or tok.v_sol_lamports is None or tok.v_tokens is None:
            return self._fail_open(position_id, "NO_QUOTE")
        if budget_lamports + self.network_fee > self.wallet.balance():
            return self._fail_open(position_id, "INSUFFICIENT_BALANCE")
        curve_sol = curve_sol_for_budget(budget_lamports, self.fees.total_bps)
        tokens = curve_tokens_out(tok.v_sol_lamports, tok.v_tokens, curve_sol)
        if tokens == 0:
            return self._fail_open(position_id, "ZERO_FILL")
        fill = Fill(buy_fill_id(position_id, intent_id), Side.BUY, tokens, budget_lamports, budget_lamports - curve_sol,
                    self.network_fee, simulated=True, ts_ns=self.clock_ns())
        with self.store.transaction():
            self.wallet.apply(fill.fill_id, -(budget_lamports + self.network_fee), f"paper buy {mint}", fill.ts_ns)
            self.ledger.record_buy_fill(position_id, fill)
        return ExecResult(True, position_id)

    def sell(self, position_id: int, intent_id: str, token_amount: int | None,
             quote_lamports: int | None = None) -> ExecResult:
        pos = self.ledger.get(position_id)
        tok = self.registry.get(pos.mint)
        if tok is None or tok.v_sol_lamports is None or tok.v_tokens is None:
            self.ledger.abort_exit(position_id, "NO_QUOTE")
            return ExecResult(False, position_id, "NO_QUOTE")
        amount = pos.token_amount if token_amount is None else token_amount
        net, fee = sell_proceeds(tok.v_sol_lamports, tok.v_tokens, amount, self.fees.total_bps)
        fill = Fill(sell_fill_id(position_id, intent_id), Side.SELL, amount, net, fee, self.network_fee, simulated=True,
                    ts_ns=self.clock_ns(), quote_lamports=quote_lamports)
        try:
            with self.store.transaction():
                self.wallet.apply(fill.fill_id, net - self.network_fee, f"paper sell {pos.mint}", fill.ts_ns)
                self.ledger.record_sell_fill(position_id, fill)
        except InsufficientBalance:
            self.ledger.abort_exit(position_id, "INSUFFICIENT_BALANCE_FOR_FEE")
            return ExecResult(False, position_id, "INSUFFICIENT_BALANCE_FOR_FEE")
        except (ValueError, FillConflict) as e:
            # a definitively rejected sell must leave the position open and retryable, never locked
            self.ledger.abort_exit(position_id, f"SELL_REJECTED: {e}")
            return ExecResult(False, position_id, f"SELL_REJECTED: {e}")
        return ExecResult(True, position_id)

    def _fail_open(self, position_id: int, reason: str) -> ExecResult:
        self.ledger.mark_open_failed(position_id, reason)
        return ExecResult(False, position_id, reason)
