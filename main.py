import sys, time, logging, configparser
from bot.binance_client import BinanceFutures
from bot.trade_engine    import TradeEngine

# ─ 설정 ───────────────────────────────────────
cfg = configparser.ConfigParser()
cfg.read("config.ini")

# ─ 로깅 ───────────────────────────────────────
level = logging.DEBUG if cfg["general"].getboolean("debug", False) else logging.INFO
logging.basicConfig(level=level,
                    format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.FileHandler("bot.log"),
                              logging.StreamHandler(sys.stdout)])

# noisy 라이브러리 억제
for lib in ("urllib3", "binance", "requests"):
    logging.getLogger(lib).setLevel(logging.WARNING)

# ─ Binance 클라이언트 ────────────────────────
client = BinanceFutures(
    api_key     = cfg["binance"]["api_key"],
    api_secret  = cfg["binance"]["api_secret"],
    recv_window = int(cfg["binance"].get("recv_window", 5000)),
)

# ─ 엔진 ───────────────────────────────────────
engine = TradeEngine(
    client   = client,
    interval = cfg["trade"]["base_interval"],
    leverage = int(cfg["trade"]["leverage"]),
    pos_pct  = float(cfg["trade"]["position_pct"]),
    sl_pct   = float(cfg["strategy"]["sl_pct"]),
    cfg      = cfg,
)

# ─ 메인 루프 ─────────────────────────────────
logging.info("=== Bot starting (DEBUG=%s) ===", cfg["general"]["debug"])
while True:
    engine.run_once()
    time.sleep(cfg["general"].getint("loop_sleep"))
