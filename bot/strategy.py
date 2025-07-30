import pandas as pd

def entry_signal(df: pd.DataFrame) -> bool:
    """LONG 진입 조건"""
    if len(df) < 3:
        return False

    c0, c1, c2 = df.iloc[-1], df.iloc[-2], df.iloc[-3]

    di_turn  = (c2.plus_di < c1.plus_di > c0.plus_di)
    obv_turn = (c2.obv     < c1.obv     > c0.obv)
    ema_ok   = c0.ema_fast > c0.ema_mid > c0.ema_slow
    acc_ok   = c0.accdist  >= c1.accdist >= c2.accdist
    atr_ok   = c0.atr      >= c1.atr     >= c2.atr

    return di_turn and obv_turn and ema_ok and acc_ok and atr_ok

def exit_signal(df: pd.DataFrame) -> bool:
    """ATR 하락 전환 → 익절"""
    if len(df) < 2:
        return False
    return df.iloc[-1].atr < df.iloc[-2].atr
