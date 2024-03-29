import math
from pancake import Prediction


def apply(psp: Prediction, df_running, current_epoch,
          base_bet, value, factor, safe_bet, bet_status):
    """
    This strategy bets exactly the same as the last known epoch.
    Martingle technique is also being applied.
    """
    current_round_stats = psp.get_round_stats(current_epoch)

    last_epoch = df_running[
        (df_running["epoch"] <= current_epoch - 2)
        &
        (df_running["reward"] != 0)
        ].max()["epoch"]

    if (last_epoch is None) or math.isnan(last_epoch):
        value = base_bet
    else:
        last_epoch_result = df_running[
            (df_running["epoch"] == last_epoch)
        ]["reward"].iloc[0]

        if last_epoch_result > 0:
            value = base_bet
        else:
            value = value * factor

    data = psp.get_round(current_epoch - 2)
    lock_price = data["lockPrice"].iloc[0]
    close_price = data["closePrice"].iloc[0]

    if factor == 0:
        if lock_price < close_price:
            # bull
            custom_factor = current_round_stats["bull_pay_ratio"] - safe_bet
        else:
            # bear
            custom_factor = current_round_stats["bear_pay_ratio"] - safe_bet

        if custom_factor < 1:
            custom_factor += safe_bet

        if bet_status["estimated_gain"] >= 0:
            loss = bet_status["recent_loss"]
        else:
            loss = max(bet_status["recent_loss"], abs(bet_status["estimated_gain"]))

        value = (loss + base_bet) / (custom_factor - 1)
        if value < base_bet:
            value = base_bet

    if lock_price > close_price:
        # bearish
        trx_hash = psp.bet_bear(value)
        position = "bear"
    elif lock_price < close_price:
        # bullish
        trx_hash = psp.bet_bull(value)
        position = "bull"
    elif lock_price == close_price:
        # draw
        trx_hash = psp.bet_bear(value)
        position = "bear"

    return position, value, trx_hash
