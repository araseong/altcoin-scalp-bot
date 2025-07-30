import pandas as pd
import ta

def add_indicators(df: pd.DataFrame, atr_window: int = 14) -> pd.DataFrame:
    df["ema_fast"] = ta.trend.ema_indicator(df.close, window=21)
    df["ema_mid"]  = ta.trend.ema_indicator(df.close, window=55)
    df["ema_slow"] = ta.trend.ema_indicator(df.close, window=144)

    adx = ta.trend.ADXIndicator(df.high, df.low, df.close, window=14)
    df["plus_di"]  = adx.adx_pos()
    df["minus_di"] = adx.adx_neg()

    df["obv"]      = ta.volume.on_balance_volume(df.close, df.volume)
    df["atr"]      = ta.volatility.average_true_range(df.high, df.low, df.close, atr_window)
    df["accdist"]  = ta.volume.acc_dist_index(df.high, df.low, df.close, df.volume)
    return df
