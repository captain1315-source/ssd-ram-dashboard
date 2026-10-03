"""build_data.py 판정 로직 테스트

실행: python scripts/test_build_data.py  (pytest로도 실행 가능)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from build_data import check_item, check_receipts, check_stock_row, grade, merge_snapshot, monthly_receipts


def lot(open_, incoming, demand, end, label=None):
    return {"label": label, "open": open_, "incoming": incoming, "demand": demand, "end": end,
            "last_price": None, "stock_price": None}


def item(lots, code="JK50-10022A"):
    return {"code": code, "name": "x", "lots": lots}


# --- 리스크 등급 (운영지침 STEP 3) ---

def test_grade_shortage_within_3_months_is_critical():
    assert grade([100, 50, -10, -20, -30, -40], [10] * 6) == ("critical", 2)


def test_grade_shortage_in_month_4_or_5_is_serious():
    assert grade([831, 763, 480, -900, -1038, -1176], [78, 68, 283, 1380, 138, 138]) == ("serious", 3)
    assert grade([100, 90, 80, 70, -5, -10], [10] * 6) == ("serious", 4)


def test_grade_shortage_in_month_6_is_warning():
    assert grade([100, 90, 80, 70, 60, -1], [10] * 6) == ("warning", 5)


def test_grade_no_shortage_but_next_month_runs_out_is_warning():
    # 마지막 월말재고가 그 달 소요보다 적으면 다음 달 부족 전환
    assert grade([1362, 1221, 997, 909, 466, 23], [73, 141, 224, 88, 443, 443]) == ("warning", None)


def test_grade_ample_stock_is_good():
    assert grade([5752, 5081, 4711, 4040, 3368, 2697], [500] * 6) == ("good", None)


def test_grade_no_demand_no_stock_is_good():
    assert grade([0] * 6, [0] * 6) == ("good", None)


# --- 정합성 검사 (운영지침 STEP 1.5) ---

def test_stock_row_sum_mismatch_is_error():
    row = {"code": "A", "total": 100, "by_supplier": {"Phison": 40, "Transcend": 50}}
    issues = check_stock_row(row)
    assert [i["level"] for i in issues] == ["error"]


def test_stock_row_sum_match_has_no_issue():
    row = {"code": "A", "total": 90, "by_supplier": {"Phison": 40, "Transcend": 50}}
    assert check_stock_row(row) == []


def test_chain_break_is_error():
    it = item([lot(100, 0, [10, 10], [90, 85])])  # 90-10=80 이어야 함
    basis, issues = check_item(it, {"total": 90})
    assert any(i["check"] == "chain" and i["level"] == "error" for i in issues)


def test_chain_with_incoming_ok_and_basis_first_end():
    it = item([lot(345, 500, [0, 42.3], [845, 802.7])])
    basis, issues = check_item(it, {"total": 845})
    assert basis == "first_end"
    assert issues == []


def test_basis_opening():
    it = item([lot(100, 0, [10], [90])])
    basis, issues = check_item(it, {"total": 100})
    assert basis == "open"
    assert issues == []


def test_stock_total_matching_nothing_is_error_with_diff():
    it = item([lot(3564, 0, [740], [2824])])
    basis, issues = check_item(it, {"total": 2823})
    assert basis is None
    assert len(issues) == 1 and issues[0]["check"] == "stock_vs_balance"
    assert "2,823" in issues[0]["msg"] and "2,824" in issues[0]["msg"]


def test_lots_are_summed_for_basis():
    it = item([lot(345, 500, [0], [845]), lot(590, 0, [73], [517], "커널앤코어")], "JK51-10012A")
    basis, issues = check_item(it, {"total": 1362})
    assert basis == "first_end" and issues == []


def test_price_gap_is_warning_not_error():
    l = lot(10, 0, [0], [10])
    l["last_price"], l["stock_price"] = 95, 27
    basis, issues = check_item(item([l]), {"total": 10})
    assert [i["level"] for i in issues] == ["warn"]


# --- 스냅샷 승격 ---

def snap(ok, tag):
    return {"tag": tag, "validation": {"ok": ok, "issues": []}}


def test_valid_snapshot_becomes_confirmed():
    out = merge_snapshot(snap(True, "new"), {"confirmed": snap(True, "old"), "pending": None})
    assert out["confirmed"]["tag"] == "new" and out["pending"] is None


def test_invalid_snapshot_keeps_previous_confirmed():
    out = merge_snapshot(snap(False, "new"), {"confirmed": snap(True, "old"), "pending": None})
    assert out["confirmed"]["tag"] == "old" and out["pending"]["tag"] == "new"


def test_invalid_snapshot_without_history_is_pending_only():
    out = merge_snapshot(snap(False, "new"), None)
    assert out["confirmed"] is None and out["pending"]["tag"] == "new"


# --- 입고단가 월 가중평균 ---
# 행: (입고일, 품번, 통화, 수량, 금액, 단가)

def test_monthly_receipts_weighted_average_usd_only():
    rows = [
        ("2026-08-21", "JK51-10012A", "USD", 1000, 30500, 30.5),
        ("2026-08-25", "JK51-10012A", "USD", 500, 15750, 31.5),
        ("2026-08-25", "JK51-10012A", "KRW", 10, 400000, 40000),   # 제외
        ("2026-09-01", "JK51-10012A", "USD", 500, 15000, 30),
    ]
    out = monthly_receipts(rows, ["JK51-10012A"])
    assert out["JK51-10012A"] == [
        {"m": "2026-08", "price": 30.83, "qty": 1500},
        {"m": "2026-09", "price": 30.0, "qty": 500},
    ]


def test_unit_price_column_wins_over_amount_divided_by_qty():
    # 원본 2026-07-13 JK50-10022A: 수량 500, 금액 42,200, 단가 42.2 (금액/수량은 84.4)
    rows = [("2026-07-13", "JK50-10022A", "USD", 500, 42200, 42.2)]
    assert monthly_receipts(rows, ["JK50-10022A"])["JK50-10022A"] == [{"m": "2026-07", "price": 42.2, "qty": 500}]


def test_missing_unit_price_falls_back_to_amount_divided_by_qty():
    rows = [("2026-07-13", "JK50-10022A", "USD", 500, 21100, None)]
    assert monthly_receipts(rows, ["JK50-10022A"])["JK50-10022A"][0]["price"] == 42.2


def test_receipt_amount_mismatch_is_flagged_as_warning():
    rows = [
        ("2026-07-13", "JK50-10022A", "USD", 500, 42200, 42.2),   # 불일치
        ("2026-04-23", "JK50-10022A", "USD", 15, 217, 14.5),      # 반올림 차이는 무시
    ]
    issues = check_receipts(rows, ["JK50-10022A"])
    assert len(issues) == 1 and issues[0]["level"] == "warn"
    assert "2026-07-13" in issues[0]["msg"] and "42,200" in issues[0]["msg"]


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
