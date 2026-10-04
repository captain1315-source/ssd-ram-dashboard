"""
dramexchange.com 가격 Excel 업데이트 스크립트
사용법:
  python update_prices.py --dram-high 56.0 --dram-low 18.8 --dram-avg 35.8 --dram-chg 0.003
                          --wafer-high 22.5 --wafer-low 16.5 --wafer-avg 20.638 --wafer-chg 0.0
                          [--contract-high 128 --contract-low 96 --contract-avg 119]

DRAM Spot:    Session High/Low/Average, Session Change (소수점, 예: 0.0165)
              --dram-* = DDR4 8Gb (1Gx8) 3200 정품 칩, --ett-* = DDR4 8Gb (1Gx8) eTT, --ddr5-* = DDR5 16Gb
Wafer Spot:   Session High/Low/Average, Average Change (소수점)
Contract:     High/Low/Average (optional, 월별 수동 입력 시)
"""

import argparse
import io
import re
import struct
from datetime import date, datetime


import os as _os
_BASE = _os.path.dirname(_os.path.abspath(__file__))
EXCEL_PATH = _os.path.join(_os.path.dirname(_BASE), "원본", "Contract, Spot Price.xlsx")


def fix_and_load_xlsx(path):
    import openpyxl
    with open(path, 'rb') as f:
        data = f.read()
    if data.rfind(b'PK\x05\x06') == -1:
        cd_entries = list(re.finditer(b'PK\x01\x02', data))
        if cd_entries:
            first_cd = cd_entries[0].start()
            num_entries = len(cd_entries)
            cd_size = len(data) - first_cd
            eocd = struct.pack('<IHHHHIIH',
                0x06054b50, 0, 0,
                num_entries, num_entries,
                cd_size, first_cd, 0)
            data = data + eocd
    return openpyxl.load_workbook(io.BytesIO(data))


def already_exists(ws, category, today, item=None):
    """같은 날짜 + 같은 Category(+Item) 행이 이미 있는지 확인.

    item을 넘기면 품목까지 일치해야 중복으로 본다.
    (DDR4/DDR5처럼 같은 Category에 여러 품목이 들어가는 경우 필요)
    """
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0] and row[1] == category:
            if item is not None and row[2] != item:
                continue
            d = row[0]
            d = d.date() if isinstance(d, datetime) else d
            if d == today:
                return True
    return False


def main():
    parser = argparse.ArgumentParser()
    # DRAM Spot
    parser.add_argument('--dram-high', type=float)
    parser.add_argument('--dram-low', type=float)
    parser.add_argument('--dram-sh', type=float, help='Session High')
    parser.add_argument('--dram-sl', type=float, help='Session Low')
    parser.add_argument('--dram-avg', type=float)
    parser.add_argument('--dram-chg', type=float, help='Session Change (소수점, 예: 0.0165)')
    parser.add_argument('--dram-weekly-high', type=float)
    parser.add_argument('--dram-weekly-low', type=float)
    # DDR5 Spot (DDR5 16Gb (2Gx8) 4800/5600)
    parser.add_argument('--ddr5-high', type=float, help='Daily High')
    parser.add_argument('--ddr5-low', type=float, help='Daily Low')
    parser.add_argument('--ddr5-sh', type=float, help='Session High')
    parser.add_argument('--ddr5-sl', type=float, help='Session Low')
    parser.add_argument('--ddr5-avg', type=float)
    parser.add_argument('--ddr5-chg', type=float, help='Session Change (소수점)')
    # DDR4 eTT Spot (DDR4 8Gb (1Gx8) eTT) — 모듈 업체용 등급 칩. 8GB 모듈 매입가와 비교하는 기준
    parser.add_argument('--ett-high', type=float, help='Daily High')
    parser.add_argument('--ett-low', type=float, help='Daily Low')
    parser.add_argument('--ett-sh', type=float, help='Session High')
    parser.add_argument('--ett-sl', type=float, help='Session Low')
    parser.add_argument('--ett-avg', type=float)
    parser.add_argument('--ett-chg', type=float, help='Session Change (소수점)')
    # Wafer Spot
    parser.add_argument('--wafer-wh', type=float, help='Weekly High')
    parser.add_argument('--wafer-wl', type=float, help='Weekly Low')
    parser.add_argument('--wafer-sh', type=float, help='Session High')
    parser.add_argument('--wafer-sl', type=float, help='Session Low')
    parser.add_argument('--wafer-avg', type=float)
    parser.add_argument('--wafer-chg', type=float, help='Average Change (소수점)')
    # Contract (optional)
    parser.add_argument('--contract-high', type=float)
    parser.add_argument('--contract-low', type=float)
    parser.add_argument('--contract-avg', type=float)
    parser.add_argument('--date', type=str, help='날짜 YYYY-MM-DD (기본: 오늘)')

    args = parser.parse_args()

    today = date.fromisoformat(args.date) if args.date else date.today()
    print(f"업데이트 날짜: {today}")

    wb = fix_and_load_xlsx(EXCEL_PATH)
    ws_spot = wb['Spot Price']
    ws_contract = wb['Contract Price']

    updated = False

    # DRAM Spot Price 추가 (DDR4)
    if args.dram_avg is not None:
        if already_exists(ws_spot, 'DRAM Spot', today, 'DDR4 8Gb (1Gx8) 3200'):
            print(f"  DRAM Spot (DDR4) {today} 이미 존재, 건너뜀")
        else:
            ws_spot.append([
                datetime.combine(today, datetime.min.time()),
                'DRAM Spot',
                'DDR4 8Gb (1Gx8) 3200',
                args.dram_high,
                args.dram_low,
                args.dram_sh if args.dram_sh is not None else args.dram_high,
                args.dram_sl if args.dram_sl is not None else args.dram_low,
                args.dram_avg,
                args.dram_chg,
                args.dram_weekly_high,
                args.dram_weekly_low,
                None,
                None,
            ])
            print(f"✓ DRAM Spot (DDR4 8Gb 3200) 추가: avg={args.dram_avg}, chg={args.dram_chg}")
            updated = True

    # DDR5 Spot Price 추가
    if args.ddr5_avg is not None:
        if already_exists(ws_spot, 'DRAM Spot', today, 'DDR5 16Gb (2Gx8) 4800/5600'):
            print(f"  DRAM Spot (DDR5) {today} 이미 존재, 건너뜀")
        else:
            ws_spot.append([
                datetime.combine(today, datetime.min.time()),
                'DRAM Spot',
                'DDR5 16Gb (2Gx8) 4800/5600',
                args.ddr5_high,
                args.ddr5_low,
                args.ddr5_sh if args.ddr5_sh is not None else args.ddr5_high,
                args.ddr5_sl if args.ddr5_sl is not None else args.ddr5_low,
                args.ddr5_avg,
                args.ddr5_chg,
                None,
                None,
                None,
                None,
            ])
            print(f"✓ DRAM Spot (DDR5 16Gb 4800/5600) 추가: avg={args.ddr5_avg}, chg={args.ddr5_chg}")
            updated = True

    # DDR4 eTT Spot Price 추가
    if args.ett_avg is not None:
        if already_exists(ws_spot, 'DRAM Spot', today, 'DDR4 8Gb (1Gx8) eTT'):
            print(f"  DRAM Spot (DDR4 eTT) {today} 이미 존재, 건너뜀")
        else:
            ws_spot.append([
                datetime.combine(today, datetime.min.time()),
                'DRAM Spot',
                'DDR4 8Gb (1Gx8) eTT',
                args.ett_high,
                args.ett_low,
                args.ett_sh if args.ett_sh is not None else args.ett_high,
                args.ett_sl if args.ett_sl is not None else args.ett_low,
                args.ett_avg,
                args.ett_chg,
                None,
                None,
                None,
                None,
            ])
            print(f"✓ DRAM Spot (DDR4 8Gb eTT) 추가: avg={args.ett_avg}, chg={args.ett_chg}")
            updated = True

    # Wafer Spot Price 추가
    if args.wafer_avg is not None:
        if already_exists(ws_spot, 'NAND Wafer Spot', today, '512Gb TLC'):
            print(f"  NAND Wafer Spot {today} 이미 존재, 건너뜀")
        else:
            ws_spot.append([
                datetime.combine(today, datetime.min.time()),
                'NAND Wafer Spot',
                '512Gb TLC',
                None,
                None,
                args.wafer_sh,
                args.wafer_sl,
                args.wafer_avg,
                args.wafer_chg,
                args.wafer_wh,
                args.wafer_wl,
                None,
                None,
            ])
            print(f"✓ NAND Wafer Spot (512Gb TLC) 추가: avg={args.wafer_avg}, chg={args.wafer_chg}")
            updated = True

    # Contract Price 추가 (선택)
    if args.contract_avg is not None:
        if already_exists(ws_contract, 'DRAM Contract', today, 'DDR4 8GB SO-DIMM'):
            print(f"  DRAM Contract Price {today} 이미 존재, 건너뜀")
        else:
            # Change 계산: 같은 품목의 직전 행과 비교
            prev_high = prev_low = prev_avg = None
            for row in ws_contract.iter_rows(min_row=2, values_only=True):
                if isinstance(row[0], datetime) and row[2] == 'DDR4 8GB SO-DIMM':
                    prev_high, prev_low, prev_avg = row[3], row[4], row[5]
            high_chg = (args.contract_high - prev_high) / prev_high if prev_high and args.contract_high is not None else None
            low_chg = (args.contract_low - prev_low) / prev_low if prev_low and args.contract_low is not None else None
            avg_chg = (args.contract_avg - prev_avg) / prev_avg if prev_avg else None

            # 시트 헤더 순서: Date | Mode | Item | High | Low | Average | High Chg | Low Chg | Avg Chg | History
            ws_contract.append([
                datetime.combine(today, datetime.min.time()),
                'DRAM Contract',
                'DDR4 8GB SO-DIMM',
                args.contract_high,
                args.contract_low,
                args.contract_avg,
                high_chg,
                low_chg,
                avg_chg,
                None,
            ])
            print(f"✓ Contract Price 추가: avg={args.contract_avg}")
            updated = True

    if updated:
        # 직접 저장 시도, 파일이 열려있으면 _pending 파일로 대기
        import os
        try:
            wb.save(EXCEL_PATH)
            print(f"✓ 저장 완료: {EXCEL_PATH}")
        except PermissionError:
            pending = EXCEL_PATH.replace('.xlsx', '_pending.xlsx')
            wb.save(pending)
            print(f"⚠️  원본 파일이 잠겨있음 (Excel 열림 상태)")
            print(f"   임시 저장: {pending}")
            print(f"   Excel을 닫은 후 _pending.xlsx를 원본에 병합하거나 이름 변경하세요")
    else:
        print("추가된 데이터 없음")


if __name__ == '__main__':
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # Windows 콘솔(cp949)에서도 ✓ 등을 출력할 수 있게
    main()
