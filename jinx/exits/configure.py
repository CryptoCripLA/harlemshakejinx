"""Owner commands for the paper exit and sizing settings. There are no defaults: until set, BigMac exits and entries
stay off (B-30, B-03). Paper only; nothing here can arm live trading.

    python -m jinx.exits.configure --db data/jinx.db show
    python -m jinx.exits.configure --db data/jinx.db set-bigmac --take-profit 50 --stop-loss 20 \
        --trailing-stop 12 --trailing-activation 20 --owner-order
    python -m jinx.exits.configure --db data/jinx.db set-kelly --file kelly.json --owner-order
    python -m jinx.exits.configure --db data/jinx.db report [--rate 107.76 --rate-source "GMGN getGasPrice"]
"""
from __future__ import annotations

import argparse
import json
import sys

from jinx.exits.policy import BIGMAC_PAPER, BigMacPaperConfig, load_bigmac_config, save_bigmac_config
from jinx.sizing.kelly import KellyConfig, load_kelly_config, save_kelly_config
from jinx.store import Store


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="jinx.exits.configure")
    p.add_argument("--db", default="data/jinx.db")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    b = sub.add_parser("set-bigmac")
    for flag in ("--take-profit", "--stop-loss", "--trailing-stop", "--trailing-activation"):
        b.add_argument(flag, type=float, required=True)
    b.add_argument("--owner-order", action="store_true")
    k = sub.add_parser("set-kelly")
    k.add_argument("--file", required=True, help="JSON: label OWNER_CONFIG, bucket_floors, priors, cap_fraction, "
                                                 "entry_filter, unscored_bucket")
    k.add_argument("--owner-order", action="store_true")
    r = sub.add_parser("report", help="the trade-by-trade report (read-only)")
    r.add_argument("--rate")
    r.add_argument("--rate-source", default="stated by the operator")
    a = p.parse_args(argv)
    store = Store(a.db)
    try:
        if a.cmd in ("set-bigmac", "set-kelly") and not a.owner_order:
            print("refused: settings change requires --owner-order", file=sys.stderr)
            return 2
        if a.cmd == "report":
            import time
            from decimal import Decimal

            from jinx.config import PAPER_START_LAMPORTS
            from jinx.ledger import PositionLedger
            from jinx.operator_view import SolUsd, trade_report
            from jinx.paper import PaperWallet
            rate = SolUsd(Decimal(a.rate), a.rate_source, time.time_ns()) if a.rate else None
            print(json.dumps(trade_report(PositionLedger(store), PaperWallet(store, PAPER_START_LAMPORTS), rate),
                             indent=1, default=str))
            return 0
        if a.cmd == "set-bigmac":
            save_bigmac_config(store, BigMacPaperConfig(a.take_profit, a.stop_loss, a.trailing_stop,
                                                        a.trailing_activation, label=BIGMAC_PAPER,
                                                        set_by="owner-order"))
        elif a.cmd == "set-kelly":
            with open(a.file) as f:
                save_kelly_config(store, KellyConfig.from_dict(json.load(f)), by="owner-order")
        bm, kl = load_bigmac_config(store), load_kelly_config(store)
        print(json.dumps({"bigmac_paper": bm and bm.to_dict(), "kelly": kl and kl.to_dict()}, indent=1))
        return 0
    except (ValueError, TypeError, KeyError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
