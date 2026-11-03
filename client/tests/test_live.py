"""Against the public instance. Deselected by default: ``pytest -m network``."""

from __future__ import annotations

import pytest

from genetrek import GeneTrek, ServerTooOld

pytestmark = pytest.mark.network


def test_the_public_instance_answers():
    with GeneTrek(server="public") as gt:
        assert gt.info()["status"] == "ok"
        assert len(gt.gene_list("hc_ndd")) > 1000
        assert gt.gene("KIAA1650")["identifiers"]["symbol"] == "SHANK3"
        if gt.api_version() < (1, 1):
            with pytest.raises(ServerTooOld):
                gt.genes(["SHANK3"])
        else:
            assert gt.genes(["SHANK3"]).height == 1
