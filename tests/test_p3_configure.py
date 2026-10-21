"""The owner's settings commands: explicit, owner-ordered, validated; nothing is set by default."""
import json

from jinx.exits.configure import main
from jinx.exits.policy import BIGMAC_PAPER, load_bigmac_config
from jinx.sizing.kelly import load_kelly_config
from jinx.store import Store


def test_set_bigmac_requires_an_owner_order_and_is_labelled(tmp_path, capsys):
    db = str(tmp_path / "j.db")
    args = ["--db", db, "set-bigmac", "--take-profit", "40", "--stop-loss", "15", "--trailing-stop", "12",
            "--trailing-activation", "20"]
    assert main(args) == 2
    assert main(args + ["--owner-order"]) == 0
    s = Store(db)
    cfg = load_bigmac_config(s)
    assert (cfg.take_profit_pct, cfg.stop_loss_pct, cfg.label, cfg.set_by) == (40, 15, BIGMAC_PAPER, "owner-order")
    s.close()


def test_set_kelly_from_a_file_and_refuse_bad_values(tmp_path):
    db = str(tmp_path / "j.db")
    good = {"label": "OWNER_CONFIG", "bucket_floors": ["60", "70"], "priors": {"60": ["0.34", "1.5"],
            "70": ["0.40", "1.6"]}, "cap_fraction": "0.05", "entry_filter": "min_score=60", "unscored_bucket": None}
    f = tmp_path / "k.json"
    f.write_text(json.dumps({**good, "cap_fraction": "2"}))
    assert main(["--db", db, "set-kelly", "--file", str(f), "--owner-order"]) == 2
    f.write_text(json.dumps(good))
    assert main(["--db", db, "set-kelly", "--file", str(f), "--owner-order"]) == 0
    s = Store(db)
    assert load_kelly_config(s).priors["70"][1] == __import__("decimal").Decimal("1.6")
    s.close()


def test_show_on_a_fresh_ledger_shows_nothing_set(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "j.db"), "show"]) == 0
    assert json.loads(capsys.readouterr().out) == {"bigmac_paper": None, "kelly": None}
