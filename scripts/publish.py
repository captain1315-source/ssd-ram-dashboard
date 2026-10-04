"""
자동 갱신: 시세 수집 → 대시보드 데이터 변환 → 바뀐 것이 있으면 GitHub에 올린다.
Windows 작업 스케줄러가 매시간 pythonw로 실행한다 (창이 뜨지 않는다).

  pythonw scripts/publish.py          평소 실행 (작업 스케줄러)
  python  scripts/publish.py --now    지금 실행하고 결과를 화면에도 보여준다 (시세는 받을 차례일 때만)
  python  scripts/publish.py --fetch   위와 같되 시세도 지금 받는다. 오후 4시 전에 쓰면 그 시각 값이 그날 값으로 남는다

시세 수집은 하루 한 번, 오후 4시 이후 첫 실행에서 한다. 재고·입고·견적 자료는 매시간 다시 읽는다.

기록은 logs/publish.log 에 남는다. 실패해도 다음 실행에서 다시 시도한다.
"""
import contextlib
import io
import subprocess
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "publish.log"
LOG_KEEP = 600                      # 남겨 둘 줄 수
FETCH_AFTER_HOUR = 16               # 시세 수집은 하루 한 번, 오후 4시 이후 첫 실행에서
FETCH_MARK = ROOT / "logs" / "last_fetch.txt"   # 마지막으로 수집한 날짜
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

sys.path.insert(0, str(Path(__file__).resolve().parent))


def git(*args):
    r = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    return r.returncode, (r.stdout + r.stderr).strip()


def captured(fn):
    """fn의 출력과 성공 여부를 돌려준다. 실패해도 예외를 밖으로 내지 않는다."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            fn()
        return True, buf.getvalue()
    except SystemExit as e:
        return False, buf.getvalue() + f"중단: {e}\n"
    except Exception:
        return False, buf.getvalue() + traceback.format_exc()


def fetch_due(now, fetched):
    """시세를 받을 차례면 수집 표시로 남길 날짜를, 아니면 None을 돌려준다. fetched: 마지막 수집 표시(YYYY-MM-DD)

    오후 4시 이후 첫 실행에서 하루 한 번 받는다. 전날 수집을 놓쳤으면(PC가 꺼져 있던 날)
    다음 날 오전 첫 실행에서 한 번 받아 전날 마감값으로 채운다.
    """
    if now.hour >= FETCH_AFTER_HOUR:
        today = f"{now:%Y-%m-%d}"
        return today if fetched < today else None
    yesterday = f"{now - timedelta(days=1):%Y-%m-%d}"
    return yesterday if fetched < yesterday else None


def summary(text):
    """변환 출력에서 품목별 상세 줄은 빼고 요약만 남긴다."""
    return [l for l in text.splitlines() if l and (not l.startswith(" ") or l.lstrip().startswith("[확인]"))]


def main():
    now, force = datetime.now(), "--fetch" in sys.argv
    show = force or "--now" in sys.argv
    lines = [f"=== {now:%Y-%m-%d %H:%M} ==="]

    fetched = FETCH_MARK.read_text(encoding="utf-8").strip() if FETCH_MARK.exists() else ""
    mark = fetch_due(now, fetched)
    if force or mark:
        import fetch_prices
        ok, out = captured(fetch_prices.main)
        if ok:
            lines += out.splitlines() or ["시세: 새로 추가할 값 없음"]
        else:
            lines += ["시세 수집 실패 (다음 실행에서 다시 시도):", *out.splitlines()[-3:]]
        if ok and mark:
            FETCH_MARK.parent.mkdir(exist_ok=True)
            FETCH_MARK.write_text(mark, encoding="utf-8")

    import build_data
    ok, out = captured(build_data.main)
    lines += summary(out) if ok else ["변환 실패, 올리지 않음:", *out.splitlines()[-6:]]

    if ok:
        _, changed = git("status", "--porcelain", "--", "data")
        if changed:
            git("add", "data/dashboard.json")
            code, msg = git("commit", "-m", f"데이터 갱신 {now:%Y-%m-%d %H:%M}")
            lines.append("커밋 완료" if code == 0 else f"커밋 실패: {msg}")
        _, ahead = git("rev-list", "--count", "origin/main..main")
        if ahead.strip().isdigit() and int(ahead) > 0:
            code, msg = git("push", "origin", "main")
            lines.append("GitHub에 올림" if code == 0 else f"올리기 실패 (다음 실행에서 다시 시도): {msg.splitlines()[-1] if msg else ''}")
        elif not changed:
            lines.append("바뀐 것 없음")

    LOG.parent.mkdir(exist_ok=True)
    old = LOG.read_text(encoding="utf-8").splitlines() if LOG.exists() else []
    LOG.write_text("\n".join((old + lines)[-LOG_KEEP:]) + "\n", encoding="utf-8")
    if show and sys.stdout:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print("\n".join(lines))


if __name__ == "__main__":
    main()
