"""The trade-by-trade report and the one compact operator view (Phase 3).

Read-only: nothing here places, changes or confirms an order, and nothing here runs between a trigger and an order.
Amounts are kept in lamports and token base units; the owner sees US dollars, converted at a stated SOL/USD rate
(GMGN `getGasPrice` -> `native_token_usd_price`) with its source and time. Without a rate no dollar figure is shown.

Accounting definitions (the ledger is the source; this only reads it):
- cost = SOL spent on buys including the curve fee; proceeds = SOL received from sells net of the curve fee;
  network fees are separate. Realized P&L of a closed position = proceeds - cost - network fees (ledger).
- An open position's mark = what selling every held token would return now (the monitor's last executable value);
  unrealized = mark - entry basis (the cost of the tokens still held). Nothing unrealized is ever added to realized.
- Slippage of a sell = proceeds quoted at the decision - proceeds actually received. Tips: none in paper (GMGN's
  priority/tip amounts are not set, B-03 C15), reported as 0. Failure costs = costs recorded on failed attempts.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from decimal import Decimal

from jinx.ledger import ACTIVE, PositionLedger, Status
from jinx.paper import PaperWallet

LAMPORTS = Decimal(1_000_000_000)


@dataclass(frozen=True)
class SolUsd:
    rate: Decimal
    source: str
    observed_ns: int

    def usd(self, lamports: int | None) -> float | None:
        return None if lamports is None else float(round(Decimal(lamports) / LAMPORTS * self.rate, 2))


def _exits(ledger: PositionLedger, pid: int) -> list[dict]:
    rows = ledger.store.conn.execute(
        "SELECT f.fill_id, f.token_amount, f.sol_lamports, f.protocol_fee_lamports, f.network_fee_lamports, f.ts_ns,"
        " f.quote_lamports, f.simulated, e.intent_id, e.reason_code, e.exit_policy, e.source_strategy, e.exit_urgency"
        " FROM fills f LEFT JOIN exit_intents e ON e.intent_id = f.exit_intent_id"
        " WHERE f.position_id = ? AND f.side = 'sell' ORDER BY f.ts_ns, f.fill_id", (pid,)).fetchall()
    return [{"intent_id": r["intent_id"], "reason": r["reason_code"], "policy": r["exit_policy"],
             "urgency": r["exit_urgency"], "tokens": r["token_amount"], "proceeds_lamports": r["sol_lamports"],
             "protocol_fee_lamports": r["protocol_fee_lamports"], "network_fee_lamports": r["network_fee_lamports"],
             "quote_lamports": r["quote_lamports"],
             "slippage_lamports": None if r["quote_lamports"] is None else r["quote_lamports"] - r["sol_lamports"],
             "simulated": bool(r["simulated"]), "ts_ns": r["ts_ns"]} for r in rows]


def _latency(ledger: PositionLedger, intent_id: str, signal_ns: int | None) -> dict:
    ev = {}
    for e in ledger.order_events(intent_id):
        ev.setdefault(e["stage"], e["ts_ns"])
    ms = lambda a, b: None if a is None or b is None else round((b - a) / 1e6, 3)
    return {"signal_to_decision_ms": ms(signal_ns, ev.get("DECIDED")),
            "decision_to_submission_ms": ms(ev.get("DECIDED"), ev.get("SUBMITTED")),
            "submission_to_fill_ms": ms(ev.get("SUBMITTED"), ev.get("FILLED")),
            "fill_kind": "simulated (paper; not a landing time)"}


def trade_report(ledger: PositionLedger, wallet: PaperWallet, sol_usd: SolUsd | None) -> dict:
    trades = []
    realized = unrealized = 0
    for pos in ledger.all():
        pid = pos.position_id
        bought = ledger.bought_tokens(pid)
        exits = _exits(ledger, pid)
        sold = sum(e["tokens"] for e in exits)
        basis = ledger.entry_basis(pid)
        failures = ledger.store.conn.execute("SELECT COUNT(*), COALESCE(SUM(cost_lamports), 0) FROM order_events"
                                             " WHERE position_id = ? AND stage = 'FAILED'", (pid,)).fetchone()
        mark = pos.last_mark_lamports if pos.status in ACTIVE else None
        t = {"position_id": pid, "mint": pos.mint, "status": pos.status.value, "simulated": pos.simulated,
             "strategy": pos.strategy, "exit_policy": pos.exit_policy, "opened_ns": pos.opened_ns,
             "closed_ns": pos.closed_ns, "tokens_bought": bought, "tokens_sold": sold, "tokens_held": pos.token_amount,
             "cost_lamports": pos.cost_lamports, "proceeds_lamports": pos.proceeds_lamports,
             "protocol_fees_lamports": pos.protocol_fees_lamports, "network_fees_lamports": pos.network_fees_lamports,
             "tips_lamports": 0, "failed_attempts": failures[0], "failure_costs_lamports": failures[1],
             "realized_pnl_lamports": pos.realized_pnl_lamports, "entry_basis_lamports": basis,
             "mark_lamports": mark, "peak_lamports": pos.peak_value_lamports,
             "unrealized_pnl_lamports": None if mark is None or basis is None else mark - basis,
             "exits": exits, "entry_latency": _latency(ledger, pos.entry_intent_id, pos.signal_ns),
             "exit_latency": [_latency(ledger, e["intent_id"], None) for e in exits if e["intent_id"]],
             "note": pos.note}
        if pos.realized_pnl_lamports is not None:
            realized += pos.realized_pnl_lamports
        if t["unrealized_pnl_lamports"] is not None:
            unrealized += t["unrealized_pnl_lamports"]
        trades.append(t)
    rec = wallet.reconcile()
    fills = ledger.store.conn.execute("SELECT SUM(simulated = 1), SUM(simulated = 0) FROM fills").fetchone()
    totals = {"positions": len(trades), "closed": sum(t["status"] == "CLOSED" for t in trades),
              "open": sum(t["status"] in ("OPEN", "PENDING_OPEN", "PENDING_CLOSE") for t in trades),
              "realized_pnl_lamports": realized, "unrealized_pnl_lamports": unrealized,
              "paper_balance_lamports": rec.balance_lamports, "paper_start_lamports": rec.starting_lamports,
              "wallet_reconciled": rec.ok, "simulated_fills": fills[0] or 0, "actual_fills": fills[1] or 0,
              "fees_lamports": sum(t["protocol_fees_lamports"] + t["network_fees_lamports"] for t in trades)}
    usd = None
    if sol_usd is not None:
        usd = {"rate": str(sol_usd.rate), "source": sol_usd.source, "observed_ns": sol_usd.observed_ns,
               "realized_pnl": sol_usd.usd(realized), "unrealized_pnl": sol_usd.usd(unrealized),
               "paper_balance": sol_usd.usd(rec.balance_lamports), "fees": sol_usd.usd(totals["fees_lamports"])}
    return {"trades": trades, "totals": totals, "usd": usd}


def operator_view(app, now_ns: int | None = None) -> dict:
    from jinx.core.bus import Lane
    from jinx.intel.replay import percentiles
    from jinx.modes import get_execution_mode, get_mode

    now = now_ns if now_ns is not None else time.time_ns()
    rate: SolUsd | None = getattr(app, "sol_usd", None)
    usd = (lambda x: rate.usd(x)) if rate else (lambda x: None)
    signals = []
    for mint, c in list(app.candidates.items())[-10:]:
        check = c.get("entry_check") or {}
        signals.append({"mint": mint, "first_detected_ns": c.get("first_detected_ns"),
                        "trade_permitted": c.get("trade_permitted"),
                        "safety": (c.get("safety") or {}).get("verdict"),
                        "entry_qualified": check.get("all_checks_pass"), "entry_reason": check.get("reserve_reason"),
                        "entry_decision": c.get("entry_decision"), "badges": app.board.badges(mint, now)})
    positions, outstanding = [], []
    for pos in app.ledger.active():
        basis = app.ledger.entry_basis(pos.position_id)
        mark = pos.last_mark_lamports
        row = {"position_id": pos.position_id, "mint": pos.mint, "status": pos.status.value,
               "strategy": pos.strategy, "exit_policy": pos.exit_policy, "age_s": round((now - pos.opened_ns) / 1e9, 1),
               "tokens_held": pos.token_amount, "entry_basis_usd": usd(basis), "mark_usd": usd(mark),
               "peak_usd": usd(pos.peak_value_lamports),
               "unrealized_usd": usd(None if mark is None or basis is None else mark - basis),
               "entry_confluence": pos.entry_score,
               "current_confluence": None if app.board.score(pos.mint) is None else str(app.board.score(pos.mint))}
        positions.append(row)
        if pos.status is not Status.OPEN:
            outstanding.append({"position_id": pos.position_id, "status": pos.status.value,
                                "entry_intent_id": pos.entry_intent_id, "exit_intent_id": pos.exit_intent_id})
    rep = trade_report(app.ledger, app.wallet, rate)
    feeds = []
    for name in ("pumpapi", "feed"):
        f = getattr(app, name, None)
        if f is not None:
            h = f.health()
            feeds.append({k: h.get(k) for k in ("name", "connected", "received", "ingest_stalled", "last_message_age_s")
                          if k in h})
    return {"generated_ns": now, "mode": get_mode(app.store).value, "execution": get_execution_mode(app.store).value,
            "live_signing": "DISABLED" if app.gateway.live is None else "ENABLED",
            "signals": signals, "open_positions": positions, "outstanding_orders": outstanding,
            "recent_exits": list(app.exit_monitor.decisions)[-10:],
            "recent_entries": list(app.entries.decisions)[-10:],
            "balance": {"lamports": rep["totals"]["paper_balance_lamports"], "usd": usd(rep["totals"]["paper_balance_lamports"]),
                        "reconciled": rep["totals"]["wallet_reconciled"]},
            "pnl": {"realized_usd": usd(rep["totals"]["realized_pnl_lamports"]),
                    "unrealized_usd": usd(rep["totals"]["unrealized_pnl_lamports"]),
                    "realized_lamports": rep["totals"]["realized_pnl_lamports"],
                    "closed": rep["totals"]["closed"], "open": rep["totals"]["open"], "kind": "simulated (paper)"},
            "latency": {"fast_lane_ms": app.bus.latency(Lane.FAST).summary(),   # rolling histogram (storage repair)
                        "entries": [t["entry_latency"] for t in rep["trades"][-5:]]},
            "feeds": feeds,
            "usd_rate": None if rate is None else {"rate": str(rate.rate), "source": rate.source,
                                                    "observed_ns": rate.observed_ns}}


def render_text(v: dict) -> str:
    """The owner's view in dollars (owner rule: no SOL wording)."""
    money = lambda x: "n/a" if x is None else (f"-${-x:,.2f}" if x < 0 else f"${x:,.2f}")
    rate = v.get("usd_rate")
    lines = [f"JINX {v['mode']} · {v['execution']} · live signing {v['live_signing']}",
             "Dollar rate: " + (f"at ${Decimal(rate['rate']):,.2f} per SOL from {rate['source']}"
                                if rate else "unavailable (no dollar figures)"),
             f"Paper balance {money(v['balance']['usd'])} (reconciled: {v['balance']['reconciled']}) · "
             f"realized {money(v['pnl']['realized_usd'])} · unrealized {money(v['pnl']['unrealized_usd'])} "
             f"· {v['pnl']['closed']} closed, {v['pnl']['open']} open (simulated)"]
    for p in v["open_positions"]:
        lines.append(f"  open #{p['position_id']} {p['mint'][:8]} age {p['age_s']}s policy {p['exit_policy']} "
                     f"worth {money(p['mark_usd'])} cost {money(p['entry_basis_usd'])} peak {money(p['peak_usd'])} "
                     f"confluence entry {p['entry_confluence']} now {p['current_confluence']}")
    for s in v["signals"][-5:]:
        d = s.get("entry_decision") or {}
        lines.append(f"  signal {s['mint'][:8]} safety {s['safety']} qualified {s['entry_qualified']} "
                     f"decision {'BUY' if d.get('ok') else d.get('reason')}")
    for e in v["recent_exits"][-5:]:
        lines.append(f"  exit #{e['position_id']} {e['reason']} by {e['source']}: {'filled' if e['ok'] else e['result']}")
    return "\n".join(lines)

