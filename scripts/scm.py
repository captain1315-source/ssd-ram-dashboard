"""MRP 앱(E:\\scm) DB 읽기 — 읽기 전용

화면(http://localhost:8000/mrp)은 로그인이 필요하므로 그 뒤의 SQLite 파일을 직접 연다.
mode=ro 로 열기 때문에 앱의 데이터는 바뀌지 않는다. 수치의 의미는 E:\\scm\\docs\\FORMULAS.md 참조.
"""
import os
import sqlite3
from datetime import date

DB_PATH = os.environ.get("SCM_DB_PATH", r"E:\scm\backend\scm.db")
PO_STATUS = ("작성", "진행", "완료")   # 엔진의 발주잔량 집계 대상과 동일


def connect(path=DB_PATH):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def month_add(month, k):
    """'YYYY-MM-01' 에 k개월을 더한 달의 1일"""
    y, m = int(month[:4]), int(month[5:7]) - 1 + k
    return date(y + m // 12, m % 12 + 1, 1).isoformat()


def _in(codes):
    return ",".join("?" * len(codes))


def base_month(con):
    return con.execute("select value from settings where key='base_month'").fetchone()[0]


def mrp(con, month, codes):
    """{품번: MRP 결과 행}. w4~w20_demand는 M+1~M+5 월 소요, m1~m5_in은 그 달 입고 예정."""
    rows = con.execute(f"select * from mrp_results where base_month=? and item_code in ({_in(codes)})",
                       (month, *codes))
    return {r["item_code"]: dict(r) for r in rows}


def receipts(con, codes):
    """현재 DB에 있는 입고 내역. 업로드할 때마다 통째로 교체되는 테이블이라 누적은 호출하는 쪽에서 한다."""
    rows = con.execute(
        f"select receipt_date, receipt_no, vendor_name, item_code, currency, fx_rate, qty, amount "
        f"from erp_receipts where item_code in ({_in(codes)}) order by receipt_date, receipt_no", codes)
    return [dict(r) for r in rows]


def purchase_orders(con, codes):
    """납기가 있는 발주 내역 (수입발주 포함). backlog = 발주수량 − 입고수량"""
    out = [dict(r) for r in con.execute(
        f"select item_code, vendor_name, effective_due_date due, order_qty, received_qty, unit_price, currency, "
        f"status, po_number from erp_purchase_orders where item_code in ({_in(codes)}) "
        f"and status in ({_in(PO_STATUS)}) and effective_due_date is not null", (*codes, *PO_STATUS))]
    out += [dict(r, received_qty=0, unit_price=None, currency=None, status="수입발주", po_number=None)
            for r in con.execute(
                f"select item_code, vendor_name, due_date due, qty order_qty from import_orders "
                f"where item_code in ({_in(codes)}) and due_date is not null", codes)]
    return out


def plan_demand_by_model(con, month, codes):
    """그 달 생산계획 소요를 최상위 모델별로: {품번: {모델코드: 수량}}"""
    out = {c: {} for c in codes}
    for code, model, qty in con.execute(
            f"select item_code, top_model_code, sum(qty) from demand_plans "
            f"where demand_date >= ? and demand_date < ? and item_code in ({_in(codes)}) "
            f"group by item_code, top_model_code", (month, month_add(month, 1), *codes)):
        out[code][model or ""] = qty or 0
    return out


def sop_by_model(con, codes):
    """S&OP 모델별 M+1~M+5 소요: {품번: {모델코드: [d1..d5]}} ('계' 집계행 제외)"""
    out = {c: {} for c in codes}
    for code, model, *ds in con.execute(
            f"select item_code, top_model_code, sum(d1), sum(d2), sum(d3), sum(d4), sum(d5) from sop_weekly "
            f"where top_model_code != '계' and item_code in ({_in(codes)}) "
            f"group by item_code, top_model_code", codes):
        out[code][model or ""] = [abs(d or 0) for d in ds]
    return out


def upload_times(con):
    """자료 종류별 마지막 업로드 시각: {'발주_RAW': 'YYYY-MM-DD HH:MM', ...}"""
    return {t: ts[:16] for t, ts in con.execute(
        "select file_type, max(created_at) from upload_logs where status != '실패' group by file_type")}
