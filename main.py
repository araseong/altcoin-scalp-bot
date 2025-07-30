import sys, time, logging, configparser
from pathlib import Path
from datetime import datetime, timezone

from bot.binance_client import BinanceFutures
from bot.trade_engine import TradeEngine
import configparser
cfg = configparser.ConfigParser(inline_comment_prefixes=(';', '#'))
cfg.read("config.ini")

debug_mode = cfg.getboolean("general", "debug", fallback=False)
log_level  = logging.DEBUG if debug_mode else logging.INFO

# ------------ 로깅 설정 ------------
logging.basicConfig(
    level=log_level,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(sys.stdout),
    ],
)

# noisy 라이브러리 억제
for lib in ("urllib3", "binance", "requests"):
    logging.getLogger(lib).setLevel(logging.WARNING)

# 'low vol_ratio' 줄 숨기기
class SkipVolFilter(logging.Filter):
    def filter(self, record):
        return "low vol_ratio" not in record.getMessage()
logging.getLogger().addFilter(SkipVolFilter())

logging.info("=== Bot starting (DEBUG=%s) ===", debug_mode)

# ------------ Binance 클라이언트 ------------
client = BinanceFutures(
    api_key     = cfg["binance"]["api_key"],
    api_secret  = cfg["binance"]["api_secret"],
    recv_window = int(cfg["binance"].get("recv_window", 5000)),
)

# ------------ 트레이드 엔진 ------------
engine = TradeEngine(
    client     = client,
    interval   = cfg["trade"]["base_interval"],
    leverage   = int(cfg["trade"]["leverage"]),
    pos_pct    = float(cfg["trade"]["position_pct"]),
    sl_pct     = float(cfg["strategy"]["sl_pct"]),
    atr_window = int(cfg["strategy"].get("atr_window", 14)),
)
engine.tuning = cfg["tuning"] if "tuning" in cfg else {}

sleep_sec = int(cfg.get("general", "loop_sleep", fallback="30"))
logging.info("loop_sleep = %s sec", sleep_sec)

# ------------ 메인 루프 ------------
while True:
    try:
        engine.run_once()
    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt – exit")
        sys.exit(0)
    except Exception as e:
        logging.error("UNCAUGHT %s: %s", type(e).__name__, e)

    time.sleep(sleep_sec)
