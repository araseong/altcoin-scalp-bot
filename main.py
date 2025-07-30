import time, logging, configparser
from bot.binance_client import BinanceFutures
from bot.trade_engine import TradeEngine

# ------------ 설정 로드 ------------
cfg = configparser.ConfigParser()
cfg.read("config.ini")

logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

client = BinanceFutures(
    api_key     = cfg["binance"]["api_key"],
    api_secret  = cfg["binance"]["api_secret"],
    recv_window = int(cfg["binance"].get("recv_window", 5000)),
)

engine = TradeEngine(
    client     = client,
    interval   = cfg["trade"]["base_interval"],
    leverage   = int(cfg["trade"]["leverage"]),
    pos_pct    = float(cfg["trade"]["position_pct"]),
    sl_pct     = float(cfg["strategy"]["sl_pct"]),
    atr_window = int(cfg["strategy"].get("atr_window", 14)),
)

# ------------ 메인 루프 ------------
if __name__ == "__main__":
    logging.info("=== Bot started ===")
    while True:
        try:
            engine.run_once()
        except Exception as e:
            logging.error("UNCAUGHT %s: %s", type(e).__name__, e)
        time.sleep(30)
