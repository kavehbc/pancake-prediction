import streamlit as st
import time
import datetime as dt
import asyncio

import strategy
from ui.expanders import update_current, update_history, update_running
from ui.params_bot import create_params_ui
import pancake
from utils.check import check_results
from utils.config import config


async def update_ui(psp, plh_update):
    while True:
        update_current(psp, plh_update)
        _ = await asyncio.sleep(1)


def main():
    st.title("PancakeSwap Prediction V2")

    if config["experimental"]["debug"]:
        st.warning(":warning: **Debug/Simulation Mode** is turned on. No actual bet will be placed."
                   " Change it in the **config.toml** file.")

    psp = pancake.Prediction()

    sidebar_params = create_params_ui(psp)
    psp = sidebar_params["psp"]

    current_epoch = psp.get_current_epoch()

    plh_current = st.empty()
    update_current(psp, plh_current)

    plh_history = st.empty()
    update_history(psp, current_epoch, plh_history)

    plh_running = st.empty()
    update_running(psp, plh_running)

    st.download_button(label="Download Running History (CSV)",
                       data=st.session_state.df_running.to_csv().encode('utf-8'),
                       file_name="running.csv",
                       mime="text/csv")

    plh_timer = st.empty()
    plh_status = st.empty()

    run_strategy = st.button("Run Strategy", disabled=psp.is_paused())

    if run_strategy:
        i_bet = 0
        btn_stop = st.button("Stop")
        value = base_bet = sidebar_params["base_bet"]

        while True:
            update_current(psp, plh_current)
            update_history(psp, current_epoch, plh_history)
            bet_status = update_running(psp, plh_running)

            if btn_stop or \
                    (0 > (sidebar_params["max_loss_threshold"] * -1) >= bet_status["estimated_gain"]) or \
                    (0 < sidebar_params["spend_threshold"] <= bet_status["total_spent"]) or \
                    (0 < sidebar_params["gain_threshold"] <= bet_status["estimated_gain"]) or \
                    (0 < sidebar_params["max_consecutive_loss"] >= bet_status["recent_loss_times"]):
                plh_status.warning("Stop criteria triggered.")
                break

            factor = sidebar_params["factor"]
            bet_epochs = sidebar_params["bet_epochs"]

            df_running = psp.get_running_df()
            current_epoch = psp.get_current_epoch()
            round_stats = psp.get_round_stats(current_epoch)

            if sidebar_params["bet_estimated_timing"]:
                bet_time = psp.bet_time
            else:
                bet_time = round_stats["round_bet_time"]

            now = dt.datetime.now()

            plh_timer.info(f"""
                          Now: {now}
                          
                          Bet: {bet_time}""")

            if now >= bet_time:
                check_results(psp)

                if df_running[df_running["epoch"] == current_epoch].shape[0] == 0:
                    if (bet_epochs == "All") \
                            or (current_epoch % 2 == 0 and bet_epochs == "Even") \
                            or (current_epoch % 2 == 1 and bet_epochs == "Odd"):

                        bet_status = update_running(psp, plh_running)
                        i_bet += 1

                        # --- START STRATEGY HERE ---
                        if sidebar_params["strategy"] == "Random":
                            position, value, trx_hash = strategy.random.apply(psp, df_running, current_epoch,
                                                                              base_bet, value, factor,
                                                                              sidebar_params["safe_bet"],
                                                                              bet_status)
                        elif sidebar_params["strategy"] == "Bullish":
                            position, value, trx_hash = strategy.bullish.apply(psp, df_running, current_epoch,
                                                                               base_bet, value, factor,
                                                                               sidebar_params["safe_bet"],
                                                                               bet_status)
                        elif sidebar_params["strategy"] == "Bearish":
                            position, value, trx_hash = strategy.bearish.apply(psp, df_running, current_epoch,
                                                                               base_bet, value, factor,
                                                                               sidebar_params["safe_bet"],
                                                                               bet_status)
                        elif sidebar_params["strategy"] == "Same-Before":
                            position, value, trx_hash = strategy.samebefore.apply(psp, df_running, current_epoch,
                                                                                  base_bet, value, factor,
                                                                                  sidebar_params["safe_bet"],
                                                                                  bet_status)
                        elif sidebar_params["strategy"] == "Trend":
                            position, value, trx_hash = strategy.trend.apply(psp, df_running, current_epoch,
                                                                             base_bet, value, factor,
                                                                             sidebar_params["safe_bet"],
                                                                             bet_status)
                        elif sidebar_params["strategy"] == "EMA":
                            position, value, trx_hash = strategy.ema.apply(psp, df_running, current_epoch,
                                                                           base_bet, value, factor,
                                                                           sidebar_params["safe_bet"],
                                                                           bet_status)

                        # --- END STRATEGY HERE ---
                        plh_status.success(f"Bet #{i_bet} - Value: {value} - Position: {position} - Trx: {trx_hash}")
                    else:
                        plh_status.info(f"Skipped")

            time.sleep(1)

    asyncio.run(update_ui(psp, plh_current))


if __name__ == '__main__':
    main()
