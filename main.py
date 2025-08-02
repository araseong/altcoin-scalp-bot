# main.py
# ────────────────────────────────────────────────────────────────
"""
Alt‑coin scalp bot launcher
───────────────────────────
• 설정 : config.ini
• 로깅 : bot.log  +  콘솔
• tmux 예 :
    PYBIN=/opt/altcoin-scalp-bot/venv/bin/python
    tmux new-session -d -s scalp "$PYBIN" -u /opt/altcoin-scalp-bot/main.py
"""

import sys, time, logging, configparser
from bot.binance_client import BinanceFutures
from bot.trade_engine    import TradeEngine

# ── 1. 설정 로드 ────────────────────────────────────────────────
cfg = configparser.ConfigParser()
cfg.read("config.ini")                   # 같은 디렉터리의 사용자 설정

# ── 2. 로깅 설정 ────────────────────────────────────────────────
log_level = logging.DEBUG if cfg.getboolean("general", "debug", fallback=False) else logging.INFO
logging.basicConfig(
    level   = log_level,
    format  = "%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler("bot.log"),
              logging.StreamHandler(sys.stdout)]
)
# noisy 라이브러리 억제
for lib in ("urllib3", "binance", "requests"):
    logging.getLogger(lib).setLevel(logging.WARNING)

# ── 3. Binance 클라이언트 ──────────────────────────────────────
client = BinanceFutures(
    api_key     = cfg["binance"]["api_key"],
    api_secret  = cfg["binance"]["api_secret"],
    recv_window = int(cfg["binance"].get("recv_window", 5000)),
)

# ── 4. TradeEngine 인스턴스 ────────────────────────────────────
engine = TradeEngine(
    client   = client,
    interval = cfg["trade"]["base_interval"],      # 예: "1m"
    leverage = int(cfg["trade"]["leverage"]),      # 예: 10
    pos_pct  = float(cfg["trade"]["position_pct"]),# 예: 0.30
    sl_pct   = float(cfg["strategy"]["sl_pct"]),   # 예: 0.02
)
# 선택적 튜닝 파라미터 주입
if "tuning" in cfg:
    engine.tuning = cfg["tuning"]

# ── 5. 메인 루프 ────────────────────────────────────────────────
if __name__ == "__main__":
    logging.info("=== Bot starting (DEBUG=%s) ===", cfg["general"].get("debug", "false"))
    loop_sleep = int(cfg["general"].get("loop_sleep", 30))  # 기본 30 초
    while True:
        try:
            engine.run_once()
        except Exception as e:
            # 어떤 예외라도 로그 남기고 루프 계속
            logging.exception("UNCAUGHT ERROR: %s", e)
        time.sleep(loop_sleep)
