from scripts.index_stack_universe import INDEX_STACKS, next_stack_index


def test_three_columns_no_nasdaq():
    assert len(INDEX_STACKS) == 3
    assert [c["id"] for c in INDEX_STACKS] == ["main", "growth", "hk"]
    codes = [it["code"] for c in INDEX_STACKS for it in c["items"]]
    assert "IXIC" not in codes
    assert "000001.SH" in codes
    assert "000680.SH" in codes
    assert "HSI" in codes


def test_next_stack_index_cycles():
    assert next_stack_index(0, 3) == 1
    assert next_stack_index(2, 3) == 0
    assert next_stack_index(0, 0) == 0
