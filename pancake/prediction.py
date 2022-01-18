from web3 import Web3
from web3.middleware import geth_poa_middleware
import datetime as dt
import pandas as pd
import numpy as np

from utils.abi import get_abi
from utils.config import config
from utils.round import round_columns


class Prediction:
    def __init__(self, address: str = None, private_key: str = None):
        self.smart_contract = config["general"]["smart_contract"]
        self.web3_provider = config["general"]["web3_provider"]
        self.abi_api = config["general"]["abi_api"]

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

        self.gas = config["tx"]["gas"]
        self.gas_price = config["tx"]["gas_price"]

        self.running_columns = ["epoch", "position", "amount", "trx_hash", "result", "claim_hash"]
        self.df_running = pd.DataFrame(columns=self.running_columns)
    # ---------------
    # PUBLIC METHODS
    # ---------------

    # Set Address & Private Key, if not through initialization
    def set_address(self, address):
        self.address = address

    def set_private_key(self, private_key):
        self.private_key = private_key

    # Running History Dataframe
    def get_running_df(self):
        return self.df_running

    # Wallet Functions
    def get_balance(self):
        try:
            my_balance = self.w3.eth.getBalance(self.address)
            my_balance = self.w3.fromWei(my_balance, 'ether')
        except:
            my_balance = None
        return my_balance

    def get_min_bet(self):
        try:
            min_bet = self.prediction_contract.functions.minBetAmount().call()
            min_bet = self.w3.fromWei(min_bet, 'ether')
        except:
            min_bet = 0
        return min_bet

    # Round/Epoch Functions
    def get_round(self, epoch):
        data = self.prediction_contract.functions.rounds(epoch).call()
        data = self._transform_round_data(data)
        return data

    def get_current_epoch(self):
        current_epoch = self.prediction_contract.functions.currentEpoch().call()
        return current_epoch

    def new_round(self):
        error = None
        try:
            current = self.get_current_epoch()
            data = self.get_round(current)

            bet_time = dt.datetime.fromtimestamp(data[2]) - dt.timedelta(seconds=config["bet"]["seconds_left"])
            # if config["bnb"]["claim"]:
            #     handleClaim()
            return [bet_time, current, error]
        except Exception as e:
            error = f"{e}"
            return [None, None, error]

    # Bet Functions
    def betBull(self, value):
        value = self.w3.toWei(value, 'ether')
        round = self.get_current_epoch()
        bull_bet = self.prediction_contract.functions.betBull(round).buildTransaction({
            'from': self.address,
            'nonce': self.w3.eth.getTransactionCount(self.address),
            'value': value,
            'gas': self.gas,
            'gasPrice': self.gas_price,
        })
        signed_tx = self.w3.eth.account.signTransaction(bull_bet, private_key=self.private_key)
        self.w3.eth.sendRawTransaction(signed_tx.rawTransaction)
        trx_hash = f'{self.w3.eth.waitForTransactionReceipt(signed_tx.hash)}'

        value = self.w3.fromWei(value, 'ether')
        self._update_running_df_bet(self, round, "bull", value, trx_hash)

        return trx_hash

    def betBear(self, value):
        value = self.w3.toWei(value, 'ether')
        round = self.get_current_epoch()
        bear_bet = self.prediction_contract.functions.betBear(round).buildTransaction({
            'from': self.address,
            'nonce': self.w3.eth.getTransactionCount(self.address),
            'value': value,
            'gas': self.gas,
            'gasPrice': self.gas_price,
        })
        signed_tx = self.w3.eth.account.signTransaction(bear_bet, private_key=self.private_key)
        self.w3.eth.sendRawTransaction(signed_tx.rawTransaction)
        trx_hash = f'{self.w3.eth.waitForTransactionReceipt(signed_tx.hash)}'

        value = self.w3.fromWei(value, 'ether')
        self._update_running_df_bet(self, round, "bear", value, trx_hash)

        return trx_hash

    # Claim Functions
    def claim(self, epochs):
        claim = self.prediction_contract.functions.claim(epochs).buildTransaction({
            'from': self.address,
            'nonce': self.w3.eth.getTransactionCount(self.address),
            'value': 0,
            'gas': 800000,
            'gasPrice': 5000000000,
        })
        signed_tx = self.w3.eth.account.signTransaction(claim, private_key=self.private_key)
        self.w3.eth.sendRawTransaction(signed_tx.rawTransaction)
        claim_hash = f'{self.w3.eth.waitForTransactionReceipt(signed_tx.hash)}'

        for epoch in epochs:
            self._update_running_df_claim(epoch, claim_hash)

        return claim_hash

    def claimable(self, epoch):
        claimable = self.prediction_contract.functions.claimable(epoch, self.address).call()
        if claimable:
            self._update_running_df_status(self, epoch, 1)
            return True
        else:
            self._update_running_df_status(self, epoch, 0)
            return False

    def fetchClaimable(self):
        epochs = []
        current = self.prediction_contract.functions.currentEpoch().call()
        epoch = current - 2
        stop = epoch - config["bnb"]["range"]

        while epoch >= stop:
            claimable = self.claimable(self, epoch)
            if claimable:
                epochs.append(epoch)
            epoch -= 1
        return epochs

    def handleClaim(self):
        trx_hash = None
        epochs = self.fetchClaimable()
        if len(epochs) > 0:
            trx_hash = self.claim(epochs)
        return len(epochs), trx_hash

    # ---------------
    # PRIVATE METHODS
    # ---------------

    def _init_w3(self):
        # BSC NODE
        self.w3 = Web3(Web3.HTTPProvider(self.web3_provider))
        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)

    def _init_abi(self):
        # V2 CONTRACT
        self.prediction_contract = self.w3.eth.contract(address=self.smart_contract,
                                                        abi=self.contract_abi)

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
        data[8] = self.w3.fromWei(data[8], 'ether')
        # uint256 bullAmount;
        data[9] = self.w3.fromWei(data[9], 'ether')
        # uint256 bearAmount;
        data[10] = self.w3.fromWei(data[10], 'ether')
        # uint256 rewardBaseCalAmount;
        data[11] = self.w3.fromWei(data[11], 'ether')
        # uint256 rewardAmount;
        data[12] = self.w3.fromWei(data[12], 'ether')
        # bool oracleCalled;

        df_current_round = pd.DataFrame(np.array([data]),
                                        columns=round_columns)
        df_current_round = df_current_round.apply(pd.to_numeric, downcast='float')

        return df_current_round

    def _update_running_df_bet(self, epoch, position, amount, trx_hash):
        # self.running_columns = ["epoch", "position", "amount", "trx_hash", "result", "claim_hash"]
        data = [epoch, position, amount, trx_hash, -1, 0]
        temp = pd.DataFrame(data=data, columns=self.running_columns)
        self.df_running = self.df_running.append(temp)

    def _update_running_df_status(self, epoch, status):
        self.df_running.loc[self.df_running.epoch == epoch, "status"] = status

    def _update_running_df_claim(self, epoch, claim_hash):
        self.df_running.loc[self.df_running.epoch == epoch, "claim_hash"] = claim_hash
