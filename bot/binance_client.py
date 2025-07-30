from typing import List, Dict
from binance.client import Client
import math

class BinanceFutures:
    """USDT‑M 선물 전용 래퍼"""

    def __init__(self, api_key: str, api_secret: str, recv_window: int = 5000):
        self.client = Client(api_key, api_secret, {"recvWindow": recv_window})
        self.alt_symbols: List[str] = self._load_usdt_m_altcoins()
        self._prec: Dict[str, int] = self._build_price_precision()

    # ---------- market info ----------
    def _load_usdt_m_altcoins(self) -> List[str]:
        info = self.client.futures_exchange_info()
        return [
            s["symbol"]
            for s in info["symbols"]
            if s["contractType"] == "PERPETUAL"
            and s["quoteAsset"] == "USDT"
            and not s["symbol"].startswith(("BTC", "ETH"))
        ]

    def _build_price_precision(self) -> Dict[str, int]:
        prec = {}
        info = self.client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] in self.alt_symbols:
                tick = float(
                    next(f for f in s["filters"] if f["filterType"] == "PRICE_FILTER")[
                        "tickSize"
                    ]
                )
                prec[s["symbol"]] = max(-int(round(math.log10(tick))), 0)
        return prec

    def price_precision(self, symbol: str) -> int:
        return self._prec[symbol]

    def klines(self, symbol: str, interval: str, limit: int = 200):
        return self.client.futures_klines(symbol=symbol, interval=interval, limit=limit)

    def top_alt_movers(self, limit: int | None = None) -> List[str]:
        """24 h 변동률 내림차순 알트 심볼"""
        tickers = self.client.futures_ticker()
        change = {
            t["symbol"]: float(t["priceChangePercent"])
            for t in tickers
            if t["symbol"] in self.alt_symbols
        }
        ordered = sorted(change, key=change.get, reverse=True)
        return ordered[:limit] if limit else ordered

    # ---------- account ----------
    def balance_usdt(self) -> float:
        bal = self.client.futures_account_balance()
        return float(next(b["balance"] for b in bal if b["asset"] == "USDT"))

    # ---------- trading ----------
    def set_leverage(self, symbol: str, leverage: int):
        self.client.futures_change_leverage(symbol=symbol, leverage=leverage)

    def open_long(self, symbol: str, qty: float):
        return self.client.futures_create_order(
            symbol=symbol, side="BUY", type="MARKET", quantity=qty
        )

    def close_position(self, symbol: str):
        pos = self.client.futures_position_information(symbol=symbol)[0]
        amt = float(pos["positionAmt"])
        if amt == 0:
            return
        side = "SELL" if amt > 0 else "BUY"
        return self.client.futures_create_order(
            symbol=symbol, side=side, type="MARKET", quantity=abs(amt)
        )

    def stop_market(self, symbol: str, side: str, qty: float, stop_price: float):
        return self.client.futures_create_order(
            symbol=symbol,
            side=side,
            type="STOP_MARKET",
            stopPrice=stop_price,
            quantity=qty,
            workingType="MARK_PRICE",
        )

    def cancel_order(self, symbol: str, order_id: int):
        self.client.futures_cancel_order(symbol=symbol, orderId=order_id)
