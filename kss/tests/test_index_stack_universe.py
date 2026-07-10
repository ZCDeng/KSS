from scripts.index_stack_universe import INDEX_STACKS, next_stack_index


def test_three_columns_no_nasdaq():
    assert len(INDEX_STACKS) == 3
    assert [c["id"] for c in INDEX_STACKS] == ["main", "growth", "hk"]
    codes = [it["code"] for c in INDEX_STACKS for it in c["items"]]
    assert "IXIC" not in codes
    assert "000001.SH" in codes
    assert "000680.SH" in codes
    assert "HSI" in codes
    assert "HSTECH" in codes


def test_hstech_uses_tushare_hktech_fetch_code():
    hk = next(c for c in INDEX_STACKS if c["id"] == "hk")
    hstech = next(it for it in hk["items"] if it["code"] == "HSTECH")
    assert hstech["name"] == "恒生科技"
    assert hstech["kind"] == "index_global"
    assert hstech.get("fetch_code") == "HKTECH"


def test_next_stack_index_cycles():
    assert next_stack_index(0, 3) == 1
    assert next_stack_index(2, 3) == 0
    assert next_stack_index(0, 0) == 0
