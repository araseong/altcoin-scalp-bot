import sys, time, logging, configparser
from bot.binance_client import BinanceFutures
from bot.trade_engine    import TradeEngine

# ── 설정 로드 ────────────────────────────────
cfg = configparser.ConfigParser()
cfg.read("config.ini")

# ── 로깅 ────────────────────────────────────
log_level = logging.DEBUG if cfg["general"].getboolean("debug", False) else logging.INFO
logging.basicConfig(
    level   = log_level,
    format  = "%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler("bot.log"),
              logging.StreamHandler(sys.stdout)]
)

# ── Binance 클라이언트 ──────────────────────
client = BinanceFutures(
    api_key     = cfg["binance"]["api_key"],
    api_secret  = cfg["binance"]["api_secret"],
    recv_window = int(cfg["binance"].get("recv_window", 5000)),
)

# ── 트레이드 엔진 ────────────────────────────
engine = TradeEngine(
    client      = client,
    interval    = cfg["trade"]["base_interval"],     # "1m"
    leverage    = int(cfg["trade"]["leverage"]),
    pos_pct     = float(cfg["trade"]["position_pct"]),
    sl_pct      = float(cfg["strategy"]["sl_pct"]),
)
engine.tuning = cfg["tuning"]      # ← 변동성 파라미터 주입

# ── 메인 루프 ────────────────────────────────
if __name__ == "__main__":
    logging.info("=== Bot starting (DEBUG=%s) ===", cfg["general"]["debug"])
    while True:
        engine.run_once()
        time.sleep(int(cfg["general"]["loop_sleep"]))
