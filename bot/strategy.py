"""
전략 진입·청산 조건 모음
────────────────────────────────────────────────────────
"""
import pandas as pd
from configparser import SectionProxy
from .indicators import add_squeeze_ind, volume_profile, rsi


def squeeze_long_trigger(df: pd.DataFrame,
                         s_cfg: SectionProxy) -> bool:
    """
    진입 조건:
      1) N 봉 연속 squeeze 상태였다가 바로 해제
      2) 현재 종가가 VAH(거래량 프로파일 상단) 돌파
      3) RSI ≥ rsi_trigger
    """
    add_squeeze_ind(df,
                    bb_window=s_cfg.getint("bb_window"),
                    kc_window=s_cfg.getint("kc_window"),
                    kc_mult  =s_cfg.getfloat("kc_mult"))

    n = s_cfg.getint("squeeze_min")
    squeeze_seq   = df.squeeze_on.rolling(n).apply(all)
    just_released = (squeeze_seq.shift(1) > 0) & (~df.squeeze_on)

    vah = volume_profile(df,
                         lookback=s_cfg.getint("vah_lookback"))
    price   = df.close.iloc[-1]

    df["rsi"] = rsi(df.close, s_cfg.getint("rsi_window"))

    return bool(just_released.iloc[-1]
                and price > vah
                and df.rsi.iloc[-1] >= s_cfg.getint("rsi_trigger"))


def exit_rsi_reversal(df: pd.DataFrame,
                      window: int = 14) -> bool:
    """RSI 가 과열권(>70) → 50 미만 급락 = 청산"""
    cur  = rsi(df.close, window).iloc[-1]
    prev = rsi(df.close, window).iloc[-2]
    return prev > 70 and cur < 50
