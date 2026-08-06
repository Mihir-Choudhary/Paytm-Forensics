"""M6 packaging sanity: bundled resources resolve and load; entry points import."""
import os

from paytmforensics.resources_util import resource_path
from paytmforensics.enrich import errorcodes
from paytmforensics.parsers.config_diag import _bank_defaults


def test_resource_path_resolves():
    p = resource_path("error_mapper.json")
    assert os.path.exists(p)


def test_errorcode_table_loads_via_resource():
    assert errorcodes.message(1103)        # decoded from bundled table


def test_bank_defaults_load_via_resource():
    d = _bank_defaults()
    assert isinstance(d, dict) and len(d) > 0


def test_entrypoints_import():
    import importlib
    for mod in ("paytmforensics.cli", "paytmforensics.gui.app", "run_gui"):
        importlib.import_module(mod)


def test_all_parsers_registered():
    from paytmforensics.parsers import REGISTRY
    names = {c.name for c in REGISTRY}
    expected = {"identity", "transactions.passbook", "transactions.chat",
                "contacts.users", "contacts.vpa_cache", "chats", "consents",
                "location.signal", "location.cookie", "jobs", "notifications",
                "config.appmanager", "config.bank", "diagnostics.bank",
                "encrypted.catalogue", "prefs", "search.recent"}
    assert expected.issubset(names), f"missing parsers: {expected - names}"
