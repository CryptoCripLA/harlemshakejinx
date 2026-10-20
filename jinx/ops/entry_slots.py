"""Owner control for JINX's global entry-slot limit.

Owner directive 2026-10-17: one global buy/position slot by default; two only
by an explicit owner command; never three; never set through environment,
config files or donor settings.

    python -m jinx.ops.entry_slots --db data/jinx.db show
    python -m jinx.ops.entry_slots --db data/jinx.db set 2 --owner-order
"""
from __future__ import annotations

import argparse
import sys
import time

from jinx.ledger import ALLOWED_SLOTS, SLOTS_KEY, PositionLedger
from jinx.store import Store


class OwnerOrderRequired(PermissionError):
    pass


def set_entry_slots(store: Store, slots: int, owner_order: bool) -> None:
    if not owner_order:
        raise OwnerOrderRequired("changing entry slots requires --owner-order")
    if slots not in ALLOWED_SLOTS:
        raise ValueError(f"entry slots must be one of {ALLOWED_SLOTS}, got {slots}")
    store.set_setting(SLOTS_KEY, str(slots), by="owner-order", now_ns=time.time_ns())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="entry_slots")
    p.add_argument("--db", default="data/jinx.db")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    s = sub.add_parser("set")
    s.add_argument("slots", type=int)
    s.add_argument("--owner-order", action="store_true")
    args = p.parse_args(argv)
    store = Store(args.db)
    try:
        if args.cmd == "set":
            set_entry_slots(store, args.slots, args.owner_order)
        ledger = PositionLedger(store)
        print(f"max_entry_slots={ledger.max_slots()} active={ledger.active_slots()}")
        return 0
    except (OwnerOrderRequired, ValueError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
