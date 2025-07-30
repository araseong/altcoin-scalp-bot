import pandas as pd
import ta

def add_indicators(df: pd.DataFrame, atr_window: int = 14) -> pd.DataFrame:
    # EMA(9·26·55)
    df["ema_fast"] = ta.trend.ema_indicator(df.close, 9)
    df["ema_mid"]  = ta.trend.ema_indicator(df.close, 26)
    df["ema_slow"] = ta.trend.ema_indicator(df.close, 55)

    # ATR
    df["atr"] = ta.volatility.AverageTrueRange(
        df.high, df.low, df.close, window=atr_window
    ).average_true_range()

    # OBV
    df["obv"] = ta.volume.on_balance_volume(df.close, df.volume)

    # DMI(+DI·–DI)
    dmi = ta.trend.ADXIndicator(df.high, df.low, df.close, window=14)
    df["plus_di"]  = dmi.adx_pos()
    df["minus_di"] = dmi.adx_neg()

    # VWAP (누적식)
    tp = (df.high + df.low + df.close) / 3
    df["vwap"] = (tp * df.volume).cumsum() / df.volume.cumsum()

    return df
