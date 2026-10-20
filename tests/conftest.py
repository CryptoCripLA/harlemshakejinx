import itertools

import pytest

from jinx.core.events import EventKind, MarketEvent, Side
from jinx.store import Store

_seq = itertools.count(1)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "jinx.db")
    yield s
    s.close()


def make_trade(mint="MINT", sig=None, trader="W1", side=Side.BUY, sol=100_000_000, tokens=1_000_000,
               v_sol=31_000_000_000, v_tokens=1_000_000_000_000, recv_ns=None, seq=None, block_time=None):
    n = next(_seq)
    return MarketEvent(kind=EventKind.TRADE, source="test", mint=mint, signature=sig or f"SIG{n}",
                       trader=trader, side=side, sol_lamports=sol, token_amount=tokens,
                       v_sol_lamports=v_sol, v_tokens=v_tokens, recv_ns=recv_ns if recv_ns is not None else n,
                       seq=seq if seq is not None else n, block_time=block_time)


def make_create(mint="MINT", sig=None, creator="DEV", recv_ns=None, seq=None, v_sol=30_000_000_000,
                v_tokens=1_073_000_000_000_000):
    n = next(_seq)
    return MarketEvent(kind=EventKind.TOKEN_CREATED, source="test", mint=mint, signature=sig or f"CSIG{n}",
                       trader=creator, v_sol_lamports=v_sol, v_tokens=v_tokens,
                       recv_ns=recv_ns if recv_ns is not None else n, seq=seq if seq is not None else n)
