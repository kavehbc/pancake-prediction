import requests
from retrying import retry
from web3 import Web3
from web3.middleware import geth_poa_middleware
import datetime as dt
import pandas as pd
import numpy as np

from utils.abi import get_abi
from utils.config import config
from utils.round import round_columns


def retry_on_http_error(exc):
    return isinstance(exc, requests.exceptions.HTTPError)


class Prediction:
    def __init__(self, address: str = None, private_key: str = None):
        self.smart_contract = config["general"]["smart_contract"]
        self.web3_provider = config["general"]["web3_provider"]
        self.abi_api = config["general"]["abi_api"]
        self.debug = config["experimental"]["debug"]

        # initializing web3 object
        self.w3 = None
        self._init_w3()

        # initializing contract ABI
        self.contract_abi = None
        self._get_abi()

        # initializing prediction object
        self.prediction_contract = ""
        self._init_abi()

        # initializing wallet
        self.address = address
        self.private_key = private_key

        # initializing wallet
        self.current_epoch = 0
        self.start_time = 0
        self.bet_time = 0
        self.lock_time = 0
        self.close_time = 0

        self.gas = config["tx"]["gas"]
        self.gas_price = config["tx"]["gas_price"]

        self.running_columns = ["epoch", "position", "amount", "trx_hash", "reward", "claim_hash"]
        self.df_running = pd.DataFrame(columns=self.running_columns)
    # ---------------
    # PUBLIC METHODS
    # ---------------

    # Set Address & Private Key, if not through initialization
    def set_address(self, address):
        self.address = address

    def set_private_key(self, private_key):
        self.private_key = private_key

    def set_df_running(self, df):
        self.df_running = df.copy()

    # IsPaused?
    def is_paused(self):
        paused = self.prediction_contract.functions.paused().call()
        return paused

    # Running History Dataframe
    def get_running_df(self):
        self.df_running = self.df_running.sort_values('epoch', ascending=False)
        self.df_running = self.df_running.reset_index(drop=True)
        return self.df_running

    # Wallet Functions
    @retry(retry_on_exception=retry_on_http_error,
           stop_max_attempt_number=config["retry"]["max_try"],
           wait_fixed=config["retry"]["delay"])
    def get_balance(self):
        try:
            my_balance = self.w3.eth.get_balance(self.address)
            my_balance = self.w3.from_wei(my_balance, 'ether')
        except:
            my_balance = None
        return my_balance

    @retry(retry_on_exception=retry_on_http_error,
           stop_max_attempt_number=config["retry"]["max_try"],
           wait_fixed=config["retry"]["delay"])
    def get_min_bet(self):
        try:
            min_bet = self.prediction_contract.functions.minBetAmount().call()
            min_bet = self.w3.from_wei(min_bet, 'ether')
        except:
            min_bet = 0
        return min_bet

    # Round/Epoch Functions
    @retry(retry_on_exception=retry_on_http_error,
           stop_max_attempt_number=config["retry"]["max_try"],
           wait_fixed=config["retry"]["delay"])
    def get_round(self, epoch):
        data = self.prediction_contract.functions.rounds(epoch).call()
        data = self._transform_round_data(data)
        return data

    def get_round_stats(self, epoch):
        df_round = self.get_round(epoch)

        total_amount = (df_round["bullAmount"] + df_round["bearAmount"]).iloc[0]
        if total_amount > 0:
            bull_ratio = ((df_round["bullAmount"] / total_amount) * 100).iloc[0]
            bear_ratio = ((df_round["bearAmount"] / total_amount) * 100).iloc[0]
            bear_pay_ratio = (total_amount / df_round["bearAmount"]).iloc[0]
            bull_pay_ratio = (total_amount / df_round["bullAmount"]).iloc[0]
        else:
            bull_ratio = None
            bear_ratio = None
            bear_pay_ratio = None
            bull_pay_ratio = None

        round_start_time = dt.datetime.fromtimestamp(df_round["startTimestamp"].iloc[0])
        round_bet_time = dt.datetime.fromtimestamp(df_round["lockTimestamp"].iloc[0]) - dt.timedelta(seconds=config["bet"]["seconds_left"])
        round_lock_time = dt.datetime.fromtimestamp(df_round["lockTimestamp"].iloc[0])
        round_close_time = dt.datetime.fromtimestamp(df_round["closeTimestamp"].iloc[0])

        return {"total_amount": total_amount,
                "bull_ratio": bull_ratio, "bear_ratio": bear_ratio,
                "bear_pay_ratio": bear_pay_ratio, "bull_pay_ratio": bull_pay_ratio,
                "round_start_time": round_start_time,
                "round_bet_time": round_bet_time,
                "round_lock_time": round_lock_time,
                "round_close_time": round_close_time}

    @retry(retry_on_exception=retry_on_http_error,
           stop_max_attempt_number=config["retry"]["max_try"],
           wait_fixed=config["retry"]["delay"])
    def get_current_epoch(self):
        current_epoch = self.prediction_contract.functions.currentEpoch().call()
        if self.current_epoch != current_epoch:
            self.current_epoch = current_epoch
            self.start_time = dt.datetime.now()
            self.lock_time = self.start_time + dt.timedelta(minutes=5)
            self.bet_time = self.lock_time - dt.timedelta(seconds=config["bet"]["seconds_left_at_estimated_time"])
            self.close_time = self.lock_time + dt.timedelta(minutes=5)
        return current_epoch

    # Bet Functions
    def bet_bull(self, value):
        epoch = self.get_current_epoch()
        if self.debug:
            trx_hash = "trx_hash_sample_string"
        else:
            value = self.w3.to_wei(value, 'ether')
            bull_bet = self.prediction_contract.functions.betBull(epoch).buildTransaction({
                'from': self.address,
                'nonce': self.w3.eth.getTransactionCount(self.address),
                'value': value,
                'gas': self.gas,
                'gasPrice': self.gas_price,
            })
            signed_trx = self.w3.eth.account.sign_transaction(bull_bet, private_key=self.private_key)
            self.w3.eth.send_raw_transaction(signed_trx.rawTransaction)
            trx_hash = f'{self.w3.eth.wait_for_transaction_receipt(signed_trx.hash)}'
            value = self.w3.from_wei(value, 'ether')

        self._update_running_df_bet(epoch, "bull", value, trx_hash)
        return trx_hash

    def bet_bear(self, value):
        epoch = self.get_current_epoch()
        if self.debug:
            trx_hash = "trx_hash_sample_string"
        else:
            value = self.w3.toWei(value, 'ether')
            bear_bet = self.prediction_contract.functions.betBear(epoch).buildTransaction({
                'from': self.address,
                'nonce': self.w3.eth.get_transaction_count(self.address),
                'value': value,
                'gas': self.gas,
                'gasPrice': self.gas_price,
            })
            signed_trx = self.w3.eth.account.sign_transaction(bear_bet, private_key=self.private_key)
            self.w3.eth.send_raw_transaction(signed_trx.raw_transaction)
            trx_hash = f'{self.w3.eth.wait_for_transaction_receipt(signed_trx.hash)}'
            value = self.w3.from_wei(value, 'ether')

        self._update_running_df_bet(epoch, "bear", value, trx_hash)
        return trx_hash

    # Claim Functions
    def claim(self, epochs):
        if self.debug:
            claim_hash = "trx_claim_hash_sample_string"
        else:
            claim = self.prediction_contract.functions.claim(epochs).buildTransaction({
                'from': self.address,
                'nonce': self.w3.eth.get_transaction_count(self.address),
                'value': 0,
                'gas': 800000,
                'gasPrice': 5000000000,
            })
            signed_trx = self.w3.eth.account.sign_transaction(claim, private_key=self.private_key)
            self.w3.eth.send_raw_transaction(signed_trx.raw_transaction)
            claim_hash = f'{self.w3.eth.wait_for_transaction_receipt(signed_trx.hash)}'

        for epoch in epochs:
            self._update_running_df_claim(epoch, claim_hash)

        return claim_hash

    @retry(retry_on_exception=retry_on_http_error,
           stop_max_attempt_number=config["retry"]["max_try"],
           wait_fixed=config["retry"]["delay"])
    def claimable(self, epoch):
        if self.debug:
            result = self._check_epoch_result(epoch)
            self._update_running_df_status(epoch, result)
            return result

        claimable = self.prediction_contract.functions.claimable(epoch, self.address).call()
        if claimable:
            # Win
            self._update_running_df_status(epoch, 1)
            return True
        else:
            # Loss
            self._update_running_df_status(epoch, 0)
            return False

    @retry(retry_on_exception=retry_on_http_error,
           stop_max_attempt_number=config["retry"]["max_try"],
           wait_fixed=config["retry"]["delay"])
    def fetch_claimable(self):
        epochs = []
        current = self.prediction_contract.functions.currentEpoch().call()
        epoch = current - 2
        stop = self.df_running["epoch"].min()

        while epoch >= stop:
            claimable = self.claimable(self, epoch)
            if claimable:
                epochs.append(epoch)
            epoch -= 1
        return epochs

    def handle_claim(self):
        trx_hash = None
        epochs = self.fetch_claimable()
        if len(epochs) > 0:
            trx_hash = self.claim(epochs)
        return len(epochs), trx_hash

    # ---------------
    # PRIVATE METHODS
    # ---------------

    @retry(retry_on_exception=retry_on_http_error,
           stop_max_attempt_number=config["retry"]["max_try"],
           wait_fixed=config["retry"]["delay"])
    def _init_w3(self):
        # BSC NODE
        self.w3 = Web3(Web3.HTTPProvider(self.web3_provider))
        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)

    def _init_abi(self):
        # V2 CONTRACT
        self.prediction_contract = self.w3.eth.contract(address=self.smart_contract,
                                                        abi=self.contract_abi)

    @retry(retry_on_exception=retry_on_http_error,
           stop_max_attempt_number=config["retry"]["max_try"],
           wait_fixed=config["retry"]["delay"])
    def _get_abi(self):
        # url_eth = self.abi_api
        # contract_address = self.w3.toChecksumAddress(self.smart_contract)
        # API_ENDPOINT = url_eth + "?module=contract&action=getabi&address=" + str(contract_address)
        # r = requests.get(url=API_ENDPOINT)
        # response = r.json()
        # self.contract_abi = json.loads(response["result"])
        self.contract_abi = get_abi()

    def _transform_round_data(self, data):
        # uint256 epoch;
        # uint256 startTimestamp;
        # uint256 lockTimestamp;
        # uint256 closeTimestamp;

        # int256 lockPrice;
        data[4] = data[4] / 100000000
        # int256 closePrice;
        data[5] = data[5] / 100000000
        # uint256 lockOracleId;
        data[6] = 0
        # uint256 closeOracleId;
        data[7] = 0
        # uint256 totalAmount;
        data[8] = self.w3.from_wei(data[8], 'ether')
        # uint256 bullAmount;
        data[9] = self.w3.from_wei(data[9], 'ether')
        # uint256 bearAmount;
        data[10] = self.w3.from_wei(data[10], 'ether')
        # uint256 rewardBaseCalAmount;
        data[11] = self.w3.from_wei(data[11], 'ether')
        # uint256 rewardAmount;
        data[12] = self.w3.from_wei(data[12], 'ether')
        # bool oracleCalled;

        df_current_round = pd.DataFrame(np.array([data]),
                                        columns=round_columns)
        df_current_round = df_current_round.apply(pd.to_numeric, downcast='float')

        return df_current_round

    def _update_running_df_bet(self, epoch, position, amount, trx_hash):
        # self.running_columns = ["epoch", "position", "amount", "trx_hash", "reward", "claim_hash"]
        data = [epoch, position, amount, trx_hash, 0, ""]
        temp = pd.DataFrame(data=[data], columns=self.running_columns)
        self.df_running = self.df_running.append(temp)

    def _update_running_df_status(self, epoch, result):
        bet_value = self.df_running[self.df_running.epoch == epoch]["amount"].iloc[0]
        bet_position = self.df_running[self.df_running.epoch == epoch]["position"].iloc[0]
        if result == 0:
            # loss
            self.df_running.loc[self.df_running.epoch == epoch, "reward"] = -1 * bet_value
        elif result == 1:
            # win
            epoch_stats = self.get_round_stats(epoch)
            if bet_position == "bull":
                pay_ratio = epoch_stats["bull_pay_ratio"]
            elif bet_position == "bear":
                pay_ratio = epoch_stats["bear_pay_ratio"]

            self.df_running.loc[self.df_running.epoch == epoch, "reward"] = (bet_value * pay_ratio) - bet_value

    def _update_running_df_claim(self, epoch, claim_hash):
        self.df_running.loc[self.df_running.epoch == epoch, "claim_hash"] = claim_hash

    def _check_epoch_result(self, epoch):
        data = self.get_round(epoch)
        lock_price = data["lockPrice"].iloc[0]
        close_price = data["closePrice"].iloc[0]

        if lock_price > close_price:
            # bearish
            condition = -1
        elif lock_price < close_price:
            # bullish
            condition = 1
        elif lock_price == close_price:
            # draw
            condition = 0

        bet_position = self.df_running[self.df_running["epoch"] == epoch]["position"].iloc[0]
        if (bet_position == "bull") and (condition == 1):
            return 1
        elif (bet_position == "bear") and (condition == -1):
            return 1
        else:
            return 0
