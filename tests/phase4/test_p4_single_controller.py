"""V20: no extra unapproved infrastructure and no independent strategy or order controller.

CLAUDE.md: "Never build two competing live execution controllers"; "No two components independently issuing orders for
the same JINX decision"; "No extra runtime services or infrastructure without a demonstrated need and owner approval";
owner storage roles (docs/INFRASTRUCTURE.md): Contabo, Redis, Neon (+ object storage, AI Gateway), Railway PostgreSQL,
Railway Hobby. Data sources approved: PumpAPI, PumpPortal (via Mako), GMGN, RugCheck (B-19).

These are source scans of the code as it is on main, so a new order path, a second runtime or a new external host
added by any builder turns this file red until it is reviewed. The allowlists name each approved site."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JINX = ROOT / "jinx"


def py_files(base=JINX):
    return [p for p in base.rglob("*.py") if "__pycache__" not in p.parts]


def calls(path: Path, names: set[str]) -> list[tuple[str, int]]:
    out = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
            if name in names:
                out.append((name, node.lineno))
    return out


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


# Who may do what. Every entry is the one approved site, verified by reading the code (2026-10-21, Phase 3 a962856):
#   jinx/confluence/entry.py  EntryCoordinator.consider_fast_path: the ONLY entry decider's submit_entry
#   jinx/exits/monitor.py     PositionMonitor.submit: the ONLY exit call (CHAD protective exits, The Executioner and
#                             BIGMAC_PAPER all go through it)
#   jinx/sim.py               the replay accounting check
ORDER_SUBMITTERS = {"jinx/sim.py", "jinx/confluence/entry.py", "jinx/exits/monitor.py"}
EXECUTORS = {"jinx/execution.py"}                      # the one gateway: calls the executor's buy / sell
FILL_WRITERS = {"jinx/paper.py"}                       # record_buy_fill / record_sell_fill callers
LEDGER_SQL = {"jinx/ledger.py", "jinx/store.py"}       # SQL writing positions / fills (store.py: schema migrations)


def test_orders_are_submitted_only_through_the_one_gateway_from_approved_sites():
    sites = {rel(p) for p in py_files() if calls(p, {"submit_entry", "submit_exit"})}
    assert sites <= ORDER_SUBMITTERS, f"new order submitter(s): {sites - ORDER_SUBMITTERS}"


def test_only_the_gateway_calls_the_executor_and_only_the_executor_records_fills():
    buy_sell = {rel(p) for p in py_files() if any(n in ("buy", "sell") for n, _ in calls(p, {"buy", "sell"}))}
    assert buy_sell <= EXECUTORS, buy_sell
    fills = {rel(p) for p in py_files() if calls(p, {"record_buy_fill", "record_sell_fill"})}
    assert fills <= FILL_WRITERS, fills
    reserve = {rel(p) for p in py_files() if calls(p, {"reserve_entry", "claim_exit", "begin_exit"})}
    assert reserve <= EXECUTORS | {"jinx/ledger.py"}, reserve


def test_only_the_ledger_writes_positions_and_fills():
    pat = re.compile(r"(INSERT\s+(OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM)\s+(positions|fills)\b", re.I)
    writers = {rel(p) for p in py_files() if pat.search(p.read_text())}
    assert writers <= LEDGER_SQL, writers


def test_gmgn_trading_is_refused_by_the_bridge_and_no_upstream_trading_client_is_built():
    bridge = (JINX / "adapters" / "gmgn" / "bridge.mjs").read_text()
    assert "Trading and state-changing methods are refused" in bridge
    for p in py_files(JINX / "adapters"):
        text = p.read_text()
        assert "PumpPortal(" not in text, p                    # Mako's trading client
        assert "submitTransaction" not in text and "sendTransaction" not in text, p


def test_the_runtime_is_one_process_with_one_long_running_service():
    units = sorted(p.name for p in (ROOT / "deploy").rglob("*.service"))
    assert units == ["jinx.service"]
    unit = (ROOT / "deploy" / "contabo" / "jinx.service").read_text()
    assert unit.count("ExecStart=") == 1 and "python -m jinx run" in unit
    mains = {rel(p) for p in py_files() if 'if __name__ == "__main__"' in p.read_text()}
    # the engine, and the owner's two settings commands (slots; BIGMAC_PAPER/Kelly settings with --owner-order and a
    # read-only report): commands that exit, not services
    assert mains <= {"jinx/__main__.py", "jinx/ops/entry_slots.py", "jinx/exits/configure.py"}, mains


def code_strings(path: Path) -> list[str]:
    """String literals in code, docstrings excluded (they cite documentation pages, which JINX never calls)."""
    tree = ast.parse(path.read_text())
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body
            and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def test_every_external_host_jinx_talks_to_is_an_approved_one():
    approved = {"stream.pumpapi.io", "pumpportal.fun", "openapi.gmgn.ai", "api.rugcheck.xyz", "127.0.0.1"}
    url = re.compile(r"(?:wss?|https?)://([A-Za-z0-9.-]+)")
    hosts = {h for p in py_files() for s in code_strings(p) for h in url.findall(s)}
    for p in JINX.rglob("*.mjs"):
        hosts |= set(url.findall(p.read_text()))
    assert "stream.pumpapi.io" in hosts and "api.rugcheck.xyz" in hosts          # the scan sees real endpoints
    assert hosts <= approved, hosts - approved


def test_the_service_settings_are_exactly_the_owner_assigned_roles():
    from jinx import config
    services = {f for f in config.Settings.__dataclass_fields__ if f.endswith(("_url", "_endpoint"))}
    assert services == {"database_url", "research_database_url", "redis_url", "s3_endpoint", "ai_gateway_url"}
