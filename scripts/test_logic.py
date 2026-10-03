"""재고 계산·입고 누적 로직 테스트

실행: python scripts/test_logic.py  (pytest로도 실행 가능)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from inventory import (consume, grade, group_demand, model_token, net_received, project, split_demand,
                       total_ends, usage_sums, worst)
from receipts import amount_mismatches, merge, monthly_receipts, resolve_maker


def lot(maker, usage, stock):
    return {"maker": maker, "usage": usage, "stock": stock}


def stocks(lots):
    return {l["maker"]: l["stock"] for l in lots}


# --- 리스크 등급 (운영지침 STEP 3) ---

def test_grade_shortage_within_3_months_is_critical():
    assert grade([100, 50, -10, -20, -30, -40], [10] * 6) == ("critical", 2)


def test_grade_shortage_in_month_4_or_5_is_serious():
    assert grade([831, 763, 480, -900, -1038, -1176], [78, 68, 283, 1380, 138, 138]) == ("serious", 3)
    assert grade([100, 90, 80, 70, -5, -10], [10] * 6) == ("serious", 4)


def test_grade_shortage_in_month_6_is_warning():
    assert grade([100, 90, 80, 70, 60, -1], [10] * 6) == ("warning", 5)


def test_grade_no_shortage_but_next_month_runs_out_is_warning():
    assert grade([1362, 1221, 997, 909, 466, 23], [73, 141, 224, 88, 443, 443]) == ("warning", None)


def test_grade_ample_stock_is_good():
    assert grade([5752, 5081, 4711, 4040, 3368, 2697], [500] * 6) == ("good", None)


def test_grade_without_demand_only_looks_at_negatives():
    # 제조사 행은 소요 없이 판정한다: 0까지 소진되는 것은 부족이 아니다
    assert grade([50, 0, 0, 0, 0, 0], None) == ("good", None)
    assert grade([50, 0, -30, -30, -30, -30], None) == ("critical", 2)


def test_worst_picks_most_severe():
    assert worst(["good", "serious", "warning"]) == "serious"


# --- 모델 → 용도 ---

def test_model_token_takes_letters_after_slash():
    assert model_token("SAPH-660/RCRB57NNB95") == "RCRB"
    assert model_token("ASTRA263/KOR54B2SNW") == "KOR"


def test_model_token_without_letters_or_slash():
    assert model_token("AES-VKT-D1B/26C5J") == "(표기 없음)"
    assert model_token("") == "(모델 없음)"
    assert model_token(None) == "(모델 없음)"


def test_code_without_slash_is_its_own_token():
    # 완제품 모델이 아닌 조립품 코드는 코드 그대로 설정에서 용도를 정할 수 있다
    assert model_token("SA95-70998A") == "SA95-70998A"
    sums, unknown = usage_sums({"SA95-70998A": 5}, {"SA95-70998A": "국내"})
    assert sums == {"국내": 5} and unknown == {}


def test_usage_sums_groups_models_and_reports_unknown_tokens_with_qty():
    by_model = {"A/KOR1": 10, "B/RCRB2": 30, "C/ZZZ9": 5, "D/ZZZ1": 1, "": 2}
    sums, unknown = usage_sums(by_model, {"KOR": "국내", "RCRB": "해외"})
    assert sums == {"국내": 10, "해외": 30, "미분류": 8}
    assert unknown == {"ZZZ": 6, "(모델 없음)": 2}


def test_split_demand_scales_to_target():
    assert split_demand(100, {"국내": 1, "해외": 3}, {}) == {"국내": 25, "해외": 75}


def test_split_demand_uses_fallback_when_month_has_no_model_data():
    assert split_demand(100, {}, {"국내": 1, "해외": 1}) == {"국내": 50, "해외": 50}


def test_split_demand_without_any_share_is_unknown():
    assert split_demand(100, {}, {}) == {"미분류": 100}
    assert split_demand(0, {"국내": 1}, {}) == {}


# --- 용도별 소진 ---

def test_domestic_demand_uses_domestic_lot_and_shortage_stays_there():
    lots = [lot("K&C", "국내", 100), lot("Tammuz", "해외", 200)]
    consume(lots, "국내", 150)
    assert stocks(lots) == {"K&C": -50, "Tammuz": 200}   # 해외용이 남아도 국내용은 부족


def test_overseas_demand_uses_overseas_lots_in_listed_order_then_shared():
    lots = [lot("Phison", "해외", 50), lot("K&C", "공용", 300), lot("Transcend", "해외", 100)]
    consume(lots, "해외", 200)
    assert stocks(lots) == {"Phison": 0, "K&C": 250, "Transcend": 0}


def test_shared_lot_serves_domestic_first_and_overseas_shortage_lands_on_overseas_lot():
    lots = [lot("K&C", "공용", 100), lot("Tammuz", "해외", 50)]
    ends = project(lots, [{"inflow": {}, "demand": {"국내": 80, "해외": 100}}])
    assert ends == [[0], [-30]]


def test_unknown_usage_demand_consumes_all_lots_in_order():
    lots = [lot("K&C", "국내", 10), lot("Tammuz", "해외", 10)]
    consume(lots, "미분류", 15)
    assert stocks(lots) == {"K&C": 0, "Tammuz": 5}


def test_single_shared_lot_takes_everything():
    lots = [lot("Transcend", "공용", 100)]
    ends = project(lots, [{"inflow": {}, "demand": {"국내": 30, "해외": 90}}])
    assert ends == [[-20]]


def test_project_adds_inflow_before_demand_and_chains_months():
    lots = [lot("K&C", "국내", 100), lot("Tammuz", "해외", 100)]
    steps = [
        {"inflow": {"Tammuz": 50}, "demand": {"국내": 40, "해외": 120}},
        {"inflow": {}, "demand": {"국내": 70, "해외": 10}},
    ]
    assert project(lots, steps) == [[60, -10], [30, 20]]
    assert stocks(lots) == {"K&C": 100, "Tammuz": 100}   # 입력은 바꾸지 않는다


def test_negative_lot_is_not_consumed_further_by_other_usage():
    lots = [lot("K&C", "공용", -20), lot("Tammuz", "해외", 30)]
    consume(lots, "해외", 50)
    assert stocks(lots) == {"K&C": -20, "Tammuz": -20}


# --- 모델별 우선 제조사 (예: TOPAZ 모델은 Crucial 먼저) ---

def test_group_demand_separates_models_with_preferred_maker():
    by_model = {"TOPAZ-1260/LPP57PNNB": 25, "TOPAZ-1270/LPP57PNNB": 25, "SAPH-660/RCRB57NNB95": 150, "ASTRA663/KOR67P2SNW95": 12}
    prefer = lambda code: "Crucial" if "topaz" in code.lower() else None
    groups, unknown = group_demand(by_model, {"LPP": "해외", "RCRB": "해외", "KOR": "국내"}, prefer)
    assert groups == {("해외", "Crucial"): 50, ("해외", None): 150, ("국내", None): 12}
    assert unknown == {}


def test_preferred_maker_is_consumed_first_then_normal_order():
    lots = [lot("K&C", "공용", 100), lot("Tammuz", "해외", 100), lot("Crucial", "해외", 30)]
    consume(lots, "해외", 50, prefer="Crucial")
    assert stocks(lots) == {"K&C": 100, "Tammuz": 80, "Crucial": 0}   # Crucial 30개를 다 쓰고 나머지는 Tammuz


def test_project_takes_preferred_demand_from_its_maker_and_the_rest_in_listed_order():
    # 실제 사례의 축소판: 해외 소요 417 중 TOPAZ 50은 Crucial에서, 나머지 367은 Tammuz에서
    lots = [lot("K&C", "공용", 915), lot("Tammuz", "해외", 1995), lot("Crucial", "해외", 3000)]
    step = {"inflow": {}, "demand": {("국내", None): 12, ("해외", None): 367, ("해외", "Crucial"): 50}}
    assert project(lots, [step]) == [[903], [1628], [2950]]


def test_split_demand_keeps_group_keys_and_custom_unknown_key():
    raw = {("해외", "Crucial"): 1, ("해외", None): 3}
    assert split_demand(100, raw, {}) == {("해외", "Crucial"): 25, ("해외", None): 75}
    assert split_demand(100, {}, {}, unknown_key=("미분류", None)) == {("미분류", None): 100}


def test_total_ends_follow_mrp_chain():
    # 월초 100 + 당월입고 10 + 입고대기 20 − 당월소요 30 = 100, 다음 달 +50 −40 = 110
    assert total_ends(100, 10, 20, [30, 40], [0, 50]) == [100, 110]


# --- 입고대기 보정: 발주 자료보다 나중에 들어온 입고를 발주잔량에서 차감 ---

def po(qty, received=0, due="2026-10-25", vendor="보이스아이", price=44.5, order_date="2026-09-28"):
    return {"item_code": "JK51-10012B", "vendor_name": vendor, "due": due, "order_date": order_date,
            "order_qty": qty, "received_qty": received, "unit_price": price, "currency": "USD"}


def rcpt(qty, d="2026-10-01", vendor="보이스아이", price=44.5):
    return {"입고일": d, "품번": "JK51-10012B", "거래처": vendor, "통화": "USD", "수량": qty, "금액": qty * price, "단가": price}


def backlog(pos):
    return [p["order_qty"] - p["received_qty"] for p in pos]


def test_receipt_after_po_snapshot_is_netted_from_matching_po():
    # 실제 사례: 9/28 발주 1,500개가 10/1 발주 자료에 미입고로 남아 있고, 10/1에 1,500개가 입고됨
    pos, log = net_received([po(1500)], [rcpt(1500)], "2026-10-01")
    assert backlog(pos) == [0]
    assert log == [{"code": "JK51-10012B", "vendor": "보이스아이", "due": "2026-10-25", "qty": 1500, "date": "2026-10-01"}]


def test_receipt_before_po_snapshot_is_already_reflected():
    pos, log = net_received([po(1500)], [rcpt(1500, d="2026-09-30")], "2026-10-01")
    assert backlog(pos) == [1500] and log == []


def test_receipt_from_other_vendor_or_price_is_not_netted():
    assert backlog(net_received([po(1500)], [rcpt(1500, vendor="지엔이")], "2026-10-01")[0]) == [1500]
    assert backlog(net_received([po(1500)], [rcpt(1500, price=52.0)], "2026-10-01")[0]) == [1500]


def test_same_day_receipt_is_skipped_when_po_already_shows_receipts():
    # 발주 자료를 올린 날의 입고는 이미 반영됐을 수 있다
    assert backlog(net_received([po(3000, received=1500)], [rcpt(1500)], "2026-10-01")[0]) == [1500]
    assert backlog(net_received([po(3000, received=1500)], [rcpt(1500, d="2026-10-02")], "2026-10-01")[0]) == [0]


def test_netting_fills_earliest_due_first_and_never_exceeds_backlog():
    pos, log = net_received([po(1000, due="2026-11-20"), po(1000, due="2026-10-25")], [rcpt(1500, d="2026-10-05")], "2026-10-01")
    assert backlog(pos) == [500, 0]
    assert [(l["due"], l["qty"]) for l in log] == [("2026-10-25", 1000), ("2026-11-20", 500)]
    assert backlog(net_received([po(1000)], [rcpt(5000, d="2026-10-05")], "2026-10-01")[0]) == [0]


def test_po_ordered_after_the_receipt_is_not_netted():
    assert backlog(net_received([po(1500, order_date="2026-10-03")], [rcpt(1500, d="2026-10-02")], "2026-10-01")[0]) == [1500]


def test_no_netting_without_po_snapshot_date():
    assert backlog(net_received([po(1500)], [rcpt(1500)], "")[0]) == [1500]


# --- 입고 누적·제조사 판별 ---

def receipt(no, code, qty, d="2026-10-01"):
    return {"입고일": d, "입고번호": no, "거래처": "V", "품번": code, "통화": "USD", "환율": 1400,
            "수량": qty, "금액": qty * 10, "단가": 10, "비고": ""}


def test_merge_skips_receipts_already_stored():
    rows = [receipt("R1", "A", 100)]
    added = merge(rows, [receipt("R1", "A", 100), receipt("R2", "A", 50), receipt("R1", "B", 100)])
    assert added == 2 and len(rows) == 3


CFG = {
    "makers": {"JK51-10012B": [{"maker": "Kernel & Core"}, {"maker": "Tammuz"}, {"maker": "Crucial"}]},
    "vendor_maker": {("지엔이", None): "Tammuz", ("보이스아이", None): "Transcend"},
    "receipt_maker": {("R9", "JK51-10012B"): "Crucial"},
}


def test_resolve_maker_prefers_per_receipt_setting():
    assert resolve_maker("JK51-10012B", "보이스아이", CFG, receipt_no="R9") == "Crucial"


def test_resolve_maker_reads_note_with_different_spelling():
    assert resolve_maker("JK51-10012B", "에스티엠", CFG, note="커널앤코어") == "Kernel & Core"


def test_resolve_maker_uses_vendor_default():
    assert resolve_maker("JK51-10012B", "지엔이", CFG) == "Tammuz"


def test_vendor_default_not_registered_for_item_is_unassigned():
    # 보이스아이의 기본 제조사 Transcend는 DDR4 8GB에 등록된 제조사가 아니다
    assert resolve_maker("JK51-10012B", "보이스아이", CFG) is None
    assert resolve_maker("JK51-10012B", "모르는 거래처", CFG) is None


def test_vendor_default_for_specific_item_wins_over_general_default():
    cfg = dict(CFG, vendor_maker={("보이스아이", None): "Transcend", ("보이스아이", "JK51-10012B"): "Crucial"})
    assert resolve_maker("JK51-10012B", "보이스아이", cfg) == "Crucial"


def test_models_without_code_can_be_mapped_in_settings():
    sums, unknown = usage_sums({"": 5, "A/KOR1": 1}, {"KOR": "국내", "(모델 없음)": "해외"})
    assert sums == {"해외": 5, "국내": 1} and unknown == {}


def test_monthly_receipts_weighted_average_usd_only():
    rows = [
        ("2026-08-21", "JK51-10012A", "USD", 1000, 30500, 30.5),
        ("2026-08-25", "JK51-10012A", "USD", 500, 15750, 31.5),
        ("2026-08-25", "JK51-10012A", "KRW", 10, 400000, 40000),   # 제외
        ("2026-09-01", "JK51-10012A", "USD", 500, 15000, 30),
    ]
    assert monthly_receipts(rows, ["JK51-10012A"])["JK51-10012A"] == [
        {"m": "2026-08", "price": 30.83, "qty": 1500},
        {"m": "2026-09", "price": 30.0, "qty": 500},
    ]


def test_unit_price_column_wins_and_falls_back_to_amount_over_qty():
    # 원본 2026-07-13 JK50-10022A: 수량 500, 금액 42,200, 단가 42.2 (금액/수량은 84.4)
    assert monthly_receipts([("2026-07-13", "A", "USD", 500, 42200, 42.2)], ["A"])["A"][0]["price"] == 42.2
    assert monthly_receipts([("2026-07-13", "A", "USD", 500, 21100, None)], ["A"])["A"][0]["price"] == 42.2


def test_amount_mismatch_ignores_rounding():
    rows = [("2026-07-13", "A", "USD", 500, 42200, 42.2), ("2026-04-23", "A", "USD", 15, 217, 14.5)]
    assert amount_mismatches(rows, ["A"]) == [("2026-07-13", "A", 500, 42.2, 42200)]


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
