import pandas as pd

# ──────────────────────────────────────────
# 진입 조건 ① : EMA 역→정배열 전환 + VWAP 상향 돌파 + +DI 우세
# ──────────────────────────────────────────
def ema_vwap_di_signal(df: pd.DataFrame) -> bool:
    if len(df) < 5:
        return False

    c0, c1 = df.iloc[-1], df.iloc[-2]

    was_bear = not (c1.ema_fast > c1.ema_mid > c1.ema_slow)
    is_bull  =     c0.ema_fast > c0.ema_mid > c0.ema_slow
    if not (was_bear and is_bull):
        return False

    if not (c1.close < c1.vwap and c0.close > c0.vwap):
        return False

    if c0.plus_di <= c0.minus_di:
        return False

    return True


# ──────────────────────────────────────────
# 진입 조건 ② : OBV·ATR 동반 상승 (lookback N 봉)
# ──────────────────────────────────────────
def obv_atr_rising(df: pd.DataFrame, lookback: int = 5) -> bool:
    if len(df) < lookback + 1:
        return False

    obv_ser = df.obv.tail(lookback + 1)
    atr_ser = df.atr.tail(lookback + 1)

    return obv_ser.is_monotonic_increasing and atr_ser.is_monotonic_increasing


# ──────────────────────────────────────────
# 손절(Exit) 조건 : OBV & +DI 동시 하락
# ──────────────────────────────────────────
def exit_signal(df: pd.DataFrame, lookback: int = 3) -> bool:
    if len(df) < lookback + 1:
        return False

    obv_diff = df.obv.diff().tail(lookback)
    di_diff  = df.plus_di.diff().tail(lookback)

    return obv_diff.lt(0).all() and di_diff.lt(0).all()
