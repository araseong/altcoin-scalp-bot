import sys
import time
import logging
import configparser
from pathlib import Path
from datetime import datetime, timezone

from bot.binance_client import BinanceFutures
from bot.trade_engine import TradeEngine

# ───────────────────────────────────────────────────────────
# 1. 설정 파일 로드
# ───────────────────────────────────────────────────────────
CFG_PATH = Path(__file__).with_name("config.ini")
if not CFG_PATH.exists():
    raise FileNotFoundError("config.ini 가 없습니다 ― 예시 파일을 복사해 작성해 주세요.")

cfg = configparser.ConfigParser()
cfg.read(CFG_PATH)

# ───────────────────────────────────────────────────────────
# 2. 로깅 설정 (파일 + 콘솔)
# ───────────────────────────────────────────────────────────
debug_mode = cfg.getboolean("general", "debug", fallback=False)
log_level = logging.DEBUG if debug_mode else logging.INFO

logging.basicConfig(
    level=log_level,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(sys.stdout),
    ],
)

# ── noisy 라이브러리 로거 억제 ─────────────────────────
for lib in ("urllib3", "binance", "requests"):
    logging.getLogger(lib).setLevel(logging.WARNING)

# ── 'Skip … low vol_ratio' 줄 숨기기 ──────────────────
class SkipVolFilter(logging.Filter):
    def filter(self, record):
        return "low vol_ratio" not in record.getMessage()

logging.getLogger().addFilter(SkipVolFilter())
# ------------------------------------------------------

logging.info("=== Bot starting (DEBUG=%s) ===", debug_mode)

# ───────────────────────────────────────────────────────────
# 3. Binance 클라이언트 인스턴스
# ───────────────────────────────────────────────────────────
client = BinanceFutures(
    api_key=cfg["binance"]["api_key"],
    api_secret=cfg["binance"]["api_secret"],
    recv_window=int(cfg["binance"].get("recv_window", 5000)),
)

# ───────────────────────────────────────────────────────────
# 4. 튜닝 파라미터 로드 (없으면 기본값 사용)
# ───────────────────────────────────────────────────────────
tuning = cfg["tuning"] if "tuning" in cfg else {}
PARAMS = {
    "di_turn": float(tuning.get("di_turn", "3.0")),        # +DI 하락 전환 폭
    "obv_z": float(tuning.get("obv_z", "-0.6")),           # OBV Z-Score 임계값
    "session_exclude": tuning.get("session_exclude", "01-04"),  # 배제 세션(UTC, 예 "01-04")
    "funding_max": float(tuning.get("funding_max", "0.05")),    # 허용 펀딩 상한 %
}

# ───────────────────────────────────────────────────────────
# 5. TradeEngine 생성 & 튜닝 파라미터 주입
# ───────────────────────────────────────────────────────────
engine = TradeEngine(
    client=client,
    interval=cfg["trade"]["base_interval"],
    leverage=int(cfg["trade"]["leverage"]),
    pos_pct=float(cfg["trade"]["position_pct"]),
    sl_pct=float(cfg["strategy"]["sl_pct"]),
    atr_window=int(cfg["strategy"].get("atr_window", 14)),
)

# 엔진 안에서 strategy.py / trade_engine.py 가 PARAMS dict 를 참조할 수 있도록
engine.tuning = PARAMS            # (속성 주입; 기존 코드와 충돌 없음)

# ───────────────────────────────────────────────────────────
# 6. 메인 루프  (sleep 간격은 config 입력 허용)
# ───────────────────────────────────────────────────────────
sleep_sec = int(cfg.get("general", "loop_sleep", fallback="30"))
logging.info("loop_sleep = %s sec • di_turn=%s • obv_z=%s",
             sleep_sec, PARAMS["di_turn"], PARAMS["obv_z"])

if __name__ == "__main__":
    while True:
        try:
            # 세션 필터 : 배제 시간대면 스캔·진입 모두 skip
            now_utc_hour = datetime.now(timezone.utc).hour
            sess_start, sess_end = map(int, PARAMS["session_exclude"].split("-"))
            if sess_start <= now_utc_hour <= sess_end:
                logging.debug("Skip by session_exclude (%02d‑%02d UTC)", sess_start, sess_end)
                time.sleep(sleep_sec)
                continue

            engine.run_once()

        except KeyboardInterrupt:
            logging.info("KeyboardInterrupt ‑ graceful shutdown")
            sys.exit(0)
        except Exception as e:
            logging.error("UNCAUGHT %s: %s", type(e).__name__, e)

        time.sleep(sleep_sec)
