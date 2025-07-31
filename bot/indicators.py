import numpy as np
import pandas as pd


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    # EMA 9 / 26 / 50
    df["ema_fast"] = df.close.ewm(span=9, adjust=False).mean()
    df["ema_mid"]  = df.close.ewm(span=26, adjust=False).mean()
    df["ema_slow"] = df.close.ewm(span=50, adjust=False).mean()

    # VWAP
    hlc3 = (df.high + df.low + df.close) / 3
    df["vwap"] = (hlc3 * df.volume).cumsum() / df.volume.cumsum()

    # DMI (+DI / –DI)
    up  = df.high.diff()
    dn  = -df.low.diff()
    plus  = up.where((up > dn) & (up > 0), 0.0)
    minus = dn.where((dn > up) & (dn > 0), 0.0)
    tr = (df.high.combine(df.close.shift(), max) -
          df.low.combine(df.close.shift(), min)).rolling(14).sum()
    df["di_plus"]  = 100 * plus.rolling(14).sum()  / tr
    df["di_minus"] = 100 * minus.rolling(14).sum() / tr

    # OBV
    direction = np.sign(df.close.diff().fillna(0))
    df["obv"] = (df.volume * direction).cumsum()

    # Accumulation/Distribution
    mfm = ((df.close - df.low) - (df.high - df.close)) / \
          (df.high - df.low + 1e-8)
    df["acdist"] = (mfm * df.volume).cumsum()

    # EMA9 (노이즈 완화)
    df["obv_ema"]    = df.obv.ewm(span=9, adjust=False).mean()
    df["acdist_ema"] = df.acdist.ewm(span=9, adjust=False).mean()

    return df
