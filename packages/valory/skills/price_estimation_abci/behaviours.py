# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2021 Valory AG
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#
# ------------------------------------------------------------------------------

"""This module contains the behaviours for the 'abci' skill."""
import binascii
import datetime
import json
import pprint
from abc import ABC
from typing import Generator, Optional, Set, Type, cast

from aea_ledger_ethereum import EthereumApi

from packages.open_aea.protocols.signing import SigningMessage
from packages.valory.contracts.gnosis_safe.contract import GnosisSafeContract
from packages.valory.contracts.offchain_aggregator.contract import (
    OffchainAggregatorContract,
)
from packages.valory.protocols.contract_api import ContractApiMessage
from packages.valory.skills.abstract_round_abci.behaviours import (
    AbstractRoundBehaviour,
    BaseState,
)
from packages.valory.skills.abstract_round_abci.utils import BenchmarkTool
from packages.valory.skills.price_estimation_abci.models import Params, SharedState
from packages.valory.skills.price_estimation_abci.payloads import (
    DeployOraclePayload,
    DeploySafePayload,
    EstimatePayload,
    FinalizationTxPayload,
    ObservationPayload,
    RandomnessPayload,
    RegistrationPayload,
    ResetPayload,
    SelectKeeperPayload,
    SignaturePayload,
    TransactionHashPayload,
    ValidatePayload,
)
from packages.valory.skills.price_estimation_abci.rounds import (
    CollectObservationRound,
    CollectSignatureRound,
    DeployOracleRound,
    DeploySafeRound,
    EstimateConsensusRound,
    FinalizationRound,
    PeriodState,
    PriceEstimationAbciApp,
    RandomnessRound,
    RandomnessStartupRound,
    RegistrationRound,
    ResetAndPauseRound,
    ResetRound,
    SelectKeeperARound,
    SelectKeeperAStartupRound,
    SelectKeeperBRound,
    SelectKeeperBStartupRound,
    TxHashRound,
    ValidateOracleRound,
    ValidateSafeRound,
    ValidateTransactionRound,
)
from packages.valory.skills.price_estimation_abci.tools import (
    hex_to_payload,
    payload_to_hex,
    random_selection,
    to_int,
)


benchmark_tool = BenchmarkTool()

SAFE_TX_GAS = 4000000  # TOFIX
ETHER_VALUE = 0  # TOFIX


class PriceEstimationBaseState(BaseState, ABC):
    """Base state behaviour for the price estimation skill."""

    @property
    def period_state(self) -> PeriodState:
        """Return the period state."""
        return cast(PeriodState, cast(SharedState, self.context.state).period_state)

    @property
    def params(self) -> Params:
        """Return the params."""
        return cast(Params, self.context.params)


class TendermintHealthcheckBehaviour(PriceEstimationBaseState):
    """Check whether Tendermint nodes are running."""

    state_id = "tendermint_healthcheck"
    matching_round = None

    _check_started: Optional[datetime.datetime] = None
    _timeout: float

    def start(self) -> None:
        """Set up the behaviour."""
        if self._check_started is None:
            self._check_started = datetime.datetime.now()
            self._timeout = self.params.max_healthcheck

    def _is_timeout_expired(self) -> bool:
        """Check if the timeout expired."""
        if self._check_started is None:
            return False  # pragma: no cover
        return datetime.datetime.now() > self._check_started + datetime.timedelta(
            0, self._timeout
        )

    def async_act(self) -> Generator:
        """Do the action."""
        self.start()
        if self._is_timeout_expired():
            # if the Tendermint node cannot update the app then the app cannot work
            raise RuntimeError("Tendermint node did not come live!")
        status = yield from self._get_status()
        try:
            json_body = json.loads(status.body.decode())
        except json.JSONDecodeError:
            self.context.logger.error(
                "Tendermint not running or accepting transactions yet, trying again!"
            )
            yield from self.sleep(self.params.sleep_time)
            return
        remote_height = int(json_body["result"]["sync_info"]["latest_block_height"])
        local_height = self.context.state.period.height
        self.context.logger.info(
            "local-height = %s, remote-height=%s", local_height, remote_height
        )
        if local_height != remote_height:
            self.context.logger.info("local height != remote height; retrying...")
            yield from self.sleep(self.params.sleep_time)
            return
        self.context.logger.info("local height == remote height; done")
        self.set_done()


class RegistrationBehaviour(PriceEstimationBaseState):
    """Register to the next round."""

    state_id = "register"
    matching_round = RegistrationRound

    def async_act(self) -> Generator:
        """
        Do the action.

        Steps:
        - Build a registration transaction.
        - Send the transaction and wait for it to be mined.
        - Wait until ABCI application transitions to the next round.
        - Go to the next behaviour state (set done event).
        """

        with benchmark_tool.measure(
            self,
        ).local():
            payload = RegistrationPayload(self.context.agent_address)

        with benchmark_tool.measure(
            self,
        ).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()


class RandomnessBehaviour(PriceEstimationBaseState):
    """Check whether Tendermint nodes are running."""

    def async_act(self) -> Generator:
        """
        Check whether tendermint is running or not.

        Steps:
        - Do a http request to the tendermint health check endpoint
        - Retry until healthcheck passes or timeout is hit.
        - If healthcheck passes set done event.
        """
        if self.context.randomness_api.is_retries_exceeded():
            # now we need to wait and see if the other agents progress the round
            with benchmark_tool.measure(
                self,
            ).consensus():
                yield from self.wait_until_round_end()
            self.set_done()
            return

        with benchmark_tool.measure(
            self,
        ).local():
            api_specs = self.context.randomness_api.get_spec()
            http_message, http_dialogue = self._build_http_request_message(
                method=api_specs["method"],
                url=api_specs["url"],
            )
            response = yield from self._do_request(http_message, http_dialogue)
            observation = self.context.randomness_api.process_response(response)

        if observation:
            self.context.logger.info(f"Retrieved DRAND values: {observation}.")
            payload = RandomnessPayload(
                self.context.agent_address,
                observation["round"],
                observation["randomness"],
            )
            with benchmark_tool.measure(
                self,
            ).consensus():
                yield from self.send_a2a_transaction(payload)
                yield from self.wait_until_round_end()

            self.set_done()
        else:
            self.context.logger.error(
                f"Could not get randomness from {self.context.randomness_api.api_id}"
            )
            yield from self.sleep(self.params.sleep_time)
            self.context.randomness_api.increment_retries()

    def clean_up(self) -> None:
        """
        Clean up the resources due to a 'stop' event.

        It can be optionally implemented by the concrete classes.
        """
        self.context.randomness_api.reset_retries()


class RandomnessAtStartupBehaviour(RandomnessBehaviour):
    """Retrive randomness at startup."""

    state_id = "retrieve_randomness_at_startup"
    matching_round = RandomnessStartupRound


class RandomnessInOperationBehaviour(RandomnessBehaviour):
    """Retrive randomness during operation."""

    state_id = "retrieve_randomness"
    matching_round = RandomnessRound


class SelectKeeperBehaviour(PriceEstimationBaseState, ABC):
    """Select the keeper agent."""

    def async_act(self) -> Generator:
        """
        Do the action.

        Steps:
        - Select a keeper randomly.
        - Send the transaction with the keeper and wait for it to be mined.
        - Wait until ABCI application transitions to the next round.
        - Go to the next behaviour state (set done event).
        """

        with benchmark_tool.measure(
            self,
        ).local():
            keeper_address = random_selection(
                sorted(self.period_state.participants),
                self.period_state.keeper_randomness,
            )

            self.context.logger.info(f"Selected a new keeper: {keeper_address}.")
            payload = SelectKeeperPayload(self.context.agent_address, keeper_address)

        with benchmark_tool.measure(
            self,
        ).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()


class SelectKeeperAAtStartupBehaviour(SelectKeeperBehaviour):
    """Select the keeper agent at startup."""

    state_id = "select_keeper_a_at_startup"
    matching_round = SelectKeeperAStartupRound


class SelectKeeperBAtStartupBehaviour(SelectKeeperBehaviour):
    """Select the keeper agent at startup."""

    state_id = "select_keeper_b_at_startup"
    matching_round = SelectKeeperBStartupRound


class SelectKeeperABehaviour(SelectKeeperBehaviour):
    """Select the keeper agent."""

    state_id = "select_keeper_a"
    matching_round = SelectKeeperARound


class SelectKeeperBBehaviour(SelectKeeperBehaviour):
    """Select the keeper agent."""

    state_id = "select_keeper_b"
    matching_round = SelectKeeperBRound


class DeploySafeBehaviour(PriceEstimationBaseState):
    """Deploy Safe."""

    state_id = "deploy_safe"
    matching_round = DeploySafeRound

    def async_act(self) -> Generator:
        """
        Do the action.

        Steps:
        - If the agent is the designated deployer, then prepare the deployment transaction and send it.
        - Otherwise, wait until the next round.
        - If a timeout is hit, set exit A event, otherwise set done event.
        """
        if self.context.agent_address != self.period_state.most_voted_keeper_address:
            yield from self._not_deployer_act()
        else:
            yield from self._deployer_act()

    def _not_deployer_act(self) -> Generator:
        """Do the non-deployer action."""
        with benchmark_tool.measure(
            self,
        ).consensus():
            yield from self.wait_until_round_end()
            self.set_done()

    def _deployer_act(self) -> Generator:
        """Do the deployer action."""

        with benchmark_tool.measure(
            self,
        ).local():
            self.context.logger.info(
                "I am the designated sender, deploying the safe contract..."
            )
            contract_address = yield from self._send_deploy_transaction()
            payload = DeploySafePayload(self.context.agent_address, contract_address)

        with benchmark_tool.measure(
            self,
        ).consensus():
            self.context.logger.info(f"Safe contract address: {contract_address}")
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()

    def _send_deploy_transaction(self) -> Generator[None, None, str]:
        owners = self.period_state.sorted_participants
        threshold = self.params.consensus_params.consensus_threshold
        contract_api_response = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_DEPLOY_TRANSACTION,  # type: ignore
            contract_address=None,
            contract_id=str(GnosisSafeContract.contract_id),
            contract_callable="get_deploy_transaction",
            owners=owners,
            threshold=threshold,
            deployer_address=self.context.agent_address,
        )
        contract_address = cast(
            str, contract_api_response.raw_transaction.body.pop("contract_address")
        )
        tx_digest = yield from self.send_raw_transaction(
            contract_api_response.raw_transaction
        )
        tx_receipt = yield from self.get_transaction_receipt(
            tx_digest,
            self.params.retry_timeout,
            self.params.retry_attempts,
        )
        if tx_receipt is None:
            raise RuntimeError("Safe deployment failed!")  # pragma: nocover
        _ = EthereumApi.get_contract_address(
            tx_receipt
        )  # returns None as the contract is created via a proxy
        self.context.logger.info(f"Deployment tx digest: {tx_digest}")
        return contract_address


class DeployOracleBehaviour(PriceEstimationBaseState):
    """Deploy Oracle."""

    state_id = "deploy_oracle"
    matching_round = DeployOracleRound

    def async_act(self) -> Generator:
        """
        Do the action.

        Steps:
        - If the agent is the designated deployer, then prepare the deployment transaction and send it.
        - Otherwise, wait until the next round.
        - If a timeout is hit, set exit A event, otherwise set done event.
        """
        if self.context.agent_address != self.period_state.most_voted_keeper_address:
            yield from self._not_deployer_act()
        else:
            yield from self._deployer_act()

    def _not_deployer_act(self) -> Generator:
        """Do the non-deployer action."""
        with benchmark_tool.measure(
            self,
        ).consensus():
            yield from self.wait_until_round_end()
            self.set_done()

    def _deployer_act(self) -> Generator:
        """Do the deployer action."""

        with benchmark_tool.measure(
            self,
        ).local():
            self.context.logger.info(
                "I am the designated sender, deploying the oracle contract..."
            )
            contract_address = yield from self._send_deploy_transaction()
            payload = DeployOraclePayload(self.context.agent_address, contract_address)

        with benchmark_tool.measure(
            self,
        ).consensus():
            self.context.logger.info(f"Oracle contract address: {contract_address}")
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()

    def _send_deploy_transaction(self) -> Generator[None, None, str]:
        min_answer = self.params.oracle_params["min_answer"]
        max_answer = self.params.oracle_params["max_answer"]
        decimals = self.params.oracle_params["decimals"]
        description = self.params.oracle_params["description"]
        contract_api_response = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_DEPLOY_TRANSACTION,  # type: ignore
            contract_address=None,
            contract_id=str(OffchainAggregatorContract.contract_id),
            contract_callable="get_deploy_transaction",
            deployer_address=self.context.agent_address,
            _minAnswer=min_answer,
            _maxAnswer=max_answer,
            _decimals=decimals,
            _description=description,
            _transmitters=[self.period_state.safe_contract_address],
            gas=10 ** 7,
        )
        tx_digest = yield from self.send_raw_transaction(
            contract_api_response.raw_transaction
        )
        tx_receipt = yield from self.get_transaction_receipt(tx_digest)
        if tx_receipt is None:
            raise RuntimeError("Oracle deployment failed!")  # pragma: nocover
        contract_address = EthereumApi.get_contract_address(tx_receipt)
        self.context.logger.info(f"Deployment tx digest: {tx_digest}")
        return contract_address


class ValidateSafeBehaviour(PriceEstimationBaseState):
    """ValidateSafe."""

    state_id = "validate_safe"
    matching_round = ValidateSafeRound

    def async_act(self) -> Generator:
        """
        Do the action.

        Steps:
        - Validate that the contract address provided by the keeper points to a valid contract.
        - Send the transaction with the validation result and wait for it to be mined.
        - Wait until ABCI application transitions to the next round.
        - Go to the next behaviour state (set done event).
        """

        with benchmark_tool.measure(
            self,
        ).local():
            is_correct = yield from self.has_correct_contract_been_deployed()
            payload = ValidatePayload(self.context.agent_address, is_correct)

        with benchmark_tool.measure(
            self,
        ).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()

    def has_correct_contract_been_deployed(self) -> Generator[None, None, bool]:
        """Contract deployment verification."""
        contract_api_response = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,  # type: ignore
            contract_address=self.period_state.safe_contract_address,
            contract_id=str(GnosisSafeContract.contract_id),
            contract_callable="verify_contract",
        )
        verified = cast(bool, contract_api_response.state.body["verified"])
        return verified


class ValidateOracleBehaviour(PriceEstimationBaseState):
    """ValidateOracle."""

    state_id = "validate_oracle"
    matching_round = ValidateOracleRound

    def async_act(self) -> Generator:
        """
        Do the action.

        Steps:
        - Validate that the contract address provided by the keeper points to a valid contract.
        - Send the transaction with the validation result and wait for it to be mined.
        - Wait until ABCI application transitions to the next round.
        - Go to the next behaviour state (set done event).
        """

        with benchmark_tool.measure(
            self,
        ).local():
            is_correct = yield from self.has_correct_contract_been_deployed()
            payload = ValidatePayload(self.context.agent_address, is_correct)

        with benchmark_tool.measure(
            self,
        ).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()

    def has_correct_contract_been_deployed(self) -> Generator[None, None, bool]:
        """Contract deployment verification."""
        contract_api_response = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,  # type: ignore
            contract_address=self.period_state.oracle_contract_address,
            contract_id=str(OffchainAggregatorContract.contract_id),
            contract_callable="verify_contract",
        )
        verified = cast(bool, contract_api_response.state.body["verified"])
        return verified


class ObserveBehaviour(PriceEstimationBaseState):
    """Observe price estimate."""

    state_id = "observe"
    matching_round = CollectObservationRound

    def async_act(self) -> Generator:
        """
        Do the action.

        Steps:
        - Ask the configured API the price of a currency.
        - If the request fails, retry until max retries are exceeded.
        - Send an observation transaction and wait for it to be mined.
        - Wait until ABCI application transitions to the next round.
        - Go to the next behaviour state (set done event).
        """
        if self.context.price_api.is_retries_exceeded():
            # now we need to wait and see if the other agents progress the round, otherwise we should restart?
            with benchmark_tool.measure(
                self,
            ).consensus():
                yield from self.wait_until_round_end()
            self.set_done()
            return

        with benchmark_tool.measure(
            self,
        ).local():
            api_specs = self.context.price_api.get_spec()
            http_message, http_dialogue = self._build_http_request_message(
                method=api_specs["method"],
                url=api_specs["url"],
                headers=api_specs["headers"],
                parameters=api_specs["parameters"],
            )
            response = yield from self._do_request(http_message, http_dialogue)
            observation = self.context.price_api.process_response(response)

        if observation:
            self.context.logger.info(
                f"Got observation of {self.context.price_api.currency_id} price in "
                + f"{self.context.price_api.convert_id} from {self.context.price_api.api_id}: "
                + f"{observation}"
            )
            payload = ObservationPayload(self.context.agent_address, observation)
            with benchmark_tool.measure(
                self,
            ).consensus():
                yield from self.send_a2a_transaction(payload)
                yield from self.wait_until_round_end()
            self.set_done()
        else:
            self.context.logger.info(
                f"Could not get price from {self.context.price_api.api_id}"
            )
            yield from self.sleep(self.params.sleep_time)
            self.context.price_api.increment_retries()

    def clean_up(self) -> None:
        """
        Clean up the resources due to a 'stop' event.

        It can be optionally implemented by the concrete classes.
        """
        self.context.price_api.reset_retries()


class EstimateBehaviour(PriceEstimationBaseState):
    """Estimate price."""

    state_id = "estimate"
    matching_round = EstimateConsensusRound

    def async_act(self) -> Generator:
        """
        Do the action.

        Steps:
        - Run the script to compute the estimate starting from the shared observations.
        - Build an estimate transaction and send the transaction and wait for it to be mined.
        - Wait until ABCI application transitions to the next round.
        - Go to the next behaviour state (set done event).
        """

        with benchmark_tool.measure(
            self,
        ).local():
            self.context.logger.info(
                "Got estimate of %s price in %s: %s",
                self.context.price_api.currency_id,
                self.context.price_api.convert_id,
                self.period_state.estimate,
            )
            payload = EstimatePayload(
                self.context.agent_address, self.period_state.estimate
            )

        with benchmark_tool.measure(
            self,
        ).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()


class TransactionHashBehaviour(PriceEstimationBaseState):
    """Share the transaction hash for the signature round."""

    state_id = "tx_hash"
    matching_round = TxHashRound

    def async_act(self) -> Generator:
        """
        Do the action.

        Steps:
        - Request the transaction hash for the safe transaction. This is the hash that needs to be signed by a threshold of agents.
        - Send the transaction hash as a transaction and wait for it to be mined.
        - Wait until ABCI application transitions to the next round.
        - Go to the next behaviour state (set done event).
        """

        with benchmark_tool.measure(
            self,
        ).local():
            contract_api_msg = yield from self.get_contract_api_response(
                performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
                contract_address=self.period_state.oracle_contract_address,
                contract_id=str(OffchainAggregatorContract.contract_id),
                contract_callable="get_latest_transmission_details",
            )
            epoch_ = cast(int, contract_api_msg.raw_transaction.body["epoch_"]) + 1
            round_ = cast(int, contract_api_msg.raw_transaction.body["round_"])
            decimals = self.params.oracle_params["decimals"]
            amount = self.period_state.most_voted_estimate
            amount_ = to_int(amount, decimals)
            contract_api_msg = yield from self.get_contract_api_response(
                performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
                contract_address=self.period_state.oracle_contract_address,
                contract_id=str(OffchainAggregatorContract.contract_id),
                contract_callable="get_transmit_data",
                epoch_=epoch_,
                round_=round_,
                amount_=amount_,
            )
            data = contract_api_msg.raw_transaction.body["data"]
            contract_api_msg = yield from self.get_contract_api_response(
                performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
                contract_address=self.period_state.safe_contract_address,
                contract_id=str(GnosisSafeContract.contract_id),
                contract_callable="get_raw_safe_transaction_hash",
                to_address=self.period_state.oracle_contract_address,
                value=ETHER_VALUE,
                data=data,
                safe_tx_gas=SAFE_TX_GAS,
            )
            safe_tx_hash = cast(str, contract_api_msg.raw_transaction.body["tx_hash"])
            safe_tx_hash = safe_tx_hash[2:]
            self.context.logger.info(f"Hash of the Safe transaction: {safe_tx_hash}")
            # temp hack:
            payload_string = payload_to_hex(safe_tx_hash, epoch_, round_, amount_)
            payload = TransactionHashPayload(self.context.agent_address, payload_string)

        with benchmark_tool.measure(
            self,
        ).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()


class SignatureBehaviour(PriceEstimationBaseState):
    """Signature state."""

    state_id = "sign"
    matching_round = CollectSignatureRound

    def async_act(self) -> Generator:
        """
        Do the action.

        Steps:
        - Request the signature of the transaction hash.
        - Send the signature as a transaction and wait for it to be mined.
        - Wait until ABCI application transitions to the next round.
        - Go to the next behaviour state (set done event).
        """

        with benchmark_tool.measure(
            self,
        ).local():
            self.context.logger.info(
                f"Consensus reached on tx hash: {self.period_state.most_voted_tx_hash}"
            )
            signature_hex = yield from self._get_safe_tx_signature()
            payload = SignaturePayload(self.context.agent_address, signature_hex)

        with benchmark_tool.measure(
            self,
        ).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()

    def _get_safe_tx_signature(self) -> Generator[None, None, str]:
        # is_deprecated_mode=True because we want to call Account.signHash,
        # which is the same used by gnosis-py
        safe_tx_hash_bytes = binascii.unhexlify(
            self.period_state.most_voted_tx_hash[:64]
        )
        self._send_signing_request(safe_tx_hash_bytes, is_deprecated_mode=True)
        signature_response = yield from self.wait_for_message()
        signature_hex = cast(SigningMessage, signature_response).signed_message.body
        # remove the leading '0x'
        signature_hex = signature_hex[2:]
        self.context.logger.info(f"Signature: {signature_hex}")
        return signature_hex


class FinalizeBehaviour(PriceEstimationBaseState):
    """Finalize state."""

    state_id = "finalize"
    matching_round = FinalizationRound

    def async_act(self) -> Generator[None, None, None]:
        """
        Do the action.

        Steps:
        - If the agent is the keeper, then prepare the transaction and send it.
        - Otherwise, wait until the next round.
        - If a timeout is hit, set exit A event, otherwise set done event.
        """
        if self.context.agent_address != self.period_state.most_voted_keeper_address:
            yield from self._not_sender_act()
        else:
            yield from self._sender_act()

    def _not_sender_act(self) -> Generator:
        """Do the non-sender action."""
        with benchmark_tool.measure(
            self,
        ).consensus():
            yield from self.wait_until_round_end()
        self.set_done()

    def _sender_act(self) -> Generator[None, None, None]:
        """Do the sender action."""

        with benchmark_tool.measure(
            self,
        ).local():
            self.context.logger.info(
                "I am the designated sender, sending the safe transaction..."
            )
            tx_digest = yield from self._send_safe_transaction()
            self.context.logger.info(
                f"Transaction digest of the final transaction: {tx_digest}"
            )
            self.context.logger.debug(
                f"Signatures: {pprint.pformat(self.period_state.participant_to_signature)}"
            )
            payload = FinalizationTxPayload(self.context.agent_address, tx_digest)

        with benchmark_tool.measure(
            self,
        ).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()

    def _send_safe_transaction(self) -> Generator[None, None, str]:
        """Send a Safe transaction using the participants' signatures."""
        _, epoch_, round_, amount_ = hex_to_payload(
            self.period_state.most_voted_tx_hash
        )
        contract_api_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.period_state.oracle_contract_address,
            contract_id=str(OffchainAggregatorContract.contract_id),
            contract_callable="get_transmit_data",
            epoch_=epoch_,
            round_=round_,
            amount_=amount_,
        )
        data = contract_api_msg.raw_transaction.body["data"]
        contract_api_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.period_state.safe_contract_address,
            contract_id=str(GnosisSafeContract.contract_id),
            contract_callable="get_raw_safe_transaction",
            sender_address=self.context.agent_address,
            owners=tuple(self.period_state.participants),
            to_address=self.period_state.oracle_contract_address,
            value=ETHER_VALUE,
            data=data,
            safe_tx_gas=SAFE_TX_GAS,
            signatures_by_owner={
                key: payload.signature
                for key, payload in self.period_state.participant_to_signature.items()
            },
        )
        tx_digest = yield from self.send_raw_transaction(
            contract_api_msg.raw_transaction
        )
        self.context.logger.info(f"Finalization tx digest: {tx_digest}")
        return tx_digest


class ValidateTransactionBehaviour(PriceEstimationBaseState):
    """ValidateTransaction."""

    state_id = "validate_transaction"
    matching_round = ValidateTransactionRound

    def async_act(self) -> Generator:
        """
        Do the action.

        Steps:
        - Validate that the transaction hash provided by the keeper points to a valid transaction.
        - Send the transaction with the validation result and wait for it to be mined.
        - Wait until ABCI application transitions to the next round.
        - Go to the next behaviour state (set done event).
        """

        with benchmark_tool.measure(
            self,
        ).local():
            is_correct = yield from self.has_transaction_been_sent()
            payload = ValidatePayload(self.context.agent_address, is_correct)

        with benchmark_tool.measure(
            self,
        ).consensus():
            yield from self.send_a2a_transaction(payload)
            yield from self.wait_until_round_end()

        self.set_done()

    def has_transaction_been_sent(self) -> Generator[None, None, Optional[bool]]:
        """Contract deployment verification."""
        response = yield from self.get_transaction_receipt(
            self.period_state.final_tx_hash,
            self.params.retry_timeout,
            self.params.retry_attempts,
        )
        if response is None:  # pragma: nocover
            self.context.logger.info(
                f"tx {self.period_state.final_tx_hash} receipt check timed out!"
            )
            return None
        is_settled = EthereumApi.is_transaction_settled(response)
        if not is_settled:  # pragma: nocover
            self.context.logger.info(
                f"tx {self.period_state.final_tx_hash} not settled!"
            )
            return False
        _, epoch_, round_, amount_ = hex_to_payload(
            self.period_state.most_voted_tx_hash
        )
        contract_api_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_RAW_TRANSACTION,  # type: ignore
            contract_address=self.period_state.oracle_contract_address,
            contract_id=str(OffchainAggregatorContract.contract_id),
            contract_callable="get_transmit_data",
            epoch_=epoch_,
            round_=round_,
            amount_=amount_,
        )
        data = contract_api_msg.raw_transaction.body["data"]
        contract_api_msg = yield from self.get_contract_api_response(
            performative=ContractApiMessage.Performative.GET_STATE,  # type: ignore
            contract_address=self.period_state.safe_contract_address,
            contract_id=str(GnosisSafeContract.contract_id),
            contract_callable="verify_tx",
            tx_hash=self.period_state.final_tx_hash,
            owners=tuple(self.period_state.participants),
            to_address=self.period_state.oracle_contract_address,
            value=ETHER_VALUE,
            data=data,
            safe_tx_gas=SAFE_TX_GAS,
            signatures_by_owner={
                key: payload.signature
                for key, payload in self.period_state.participant_to_signature.items()
            },
        )
        if contract_api_msg.performative != ContractApiMessage.Performative.STATE:
            return False  # pragma: nocover
        verified = cast(bool, contract_api_msg.state.body["verified"])
        verified_log = (
            f"Verified result: {verified}"
            if verified
            else f"Verified result: {verified}, all: {contract_api_msg.state.body}"
        )
        self.context.logger.info(verified_log)
        return verified


class BaseResetBehaviour(PriceEstimationBaseState):
    """Reset state."""

    pause = True

    def async_act(self) -> Generator:
        """
        Do the action.

        Steps:
        - Trivially log the state.
        - Sleep for configured interval.
        - Build a registration transaction.
        - Send the transaction and wait for it to be mined.
        - Wait until ABCI application transitions to the next round.
        - Go to the next behaviour state (set done event).
        """
        if self.pause:
            if (
                self.period_state.is_most_voted_estimate_set
                and self.period_state.is_final_tx_hash_set
            ):
                self.context.logger.info(
                    f"Finalized estimate: {self.period_state.most_voted_estimate} with transaction hash: {self.period_state.final_tx_hash}"
                )
            else:
                self.context.logger.info("Finalized estimate not available.")
            self.context.logger.info("Period end.")
            benchmark_tool.save()

            yield from self.sleep(self.params.observation_interval)
        else:
            self.context.logger.info(
                f"Period {self.period_state.period_count} was not finished. Resetting!"
            )

        payload = ResetPayload(
            self.context.agent_address, self.period_state.period_count + 1
        )

        yield from self.send_a2a_transaction(payload)
        yield from self.wait_until_round_end()
        self.set_done()


class ResetBehaviour(BaseResetBehaviour):
    """Reset state."""

    matching_round = ResetRound
    state_id = "reset"
    pause = False


class ResetAndPauseBehaviour(BaseResetBehaviour):
    """Reset state."""

    matching_round = ResetAndPauseRound
    state_id = "reset_and_pause"
    pause = True


class PriceEstimationConsensusBehaviour(AbstractRoundBehaviour):
    """This behaviour manages the consensus stages for the price estimation."""

    initial_state_cls = TendermintHealthcheckBehaviour
    abci_app_cls = PriceEstimationAbciApp  # type: ignore
    behaviour_states: Set[Type[PriceEstimationBaseState]] = {  # type: ignore
        TendermintHealthcheckBehaviour,  # type: ignore
        RegistrationBehaviour,  # type: ignore
        RandomnessAtStartupBehaviour,  # type: ignore
        SelectKeeperAAtStartupBehaviour,  # type: ignore
        DeploySafeBehaviour,  # type: ignore
        ValidateSafeBehaviour,  # type: ignore
        SelectKeeperBAtStartupBehaviour,  # type: ignore
        DeployOracleBehaviour,  # type: ignore
        ValidateOracleBehaviour,  # type: ignore
        RandomnessInOperationBehaviour,  # type: ignore
        SelectKeeperABehaviour,  # type: ignore
        ObserveBehaviour,  # type: ignore
        EstimateBehaviour,  # type: ignore
        TransactionHashBehaviour,  # type: ignore
        SignatureBehaviour,  # type: ignore
        FinalizeBehaviour,  # type: ignore
        ValidateTransactionBehaviour,  # type: ignore
        SelectKeeperBBehaviour,  # type: ignore
        ResetBehaviour,  # type: ignore
        ResetAndPauseBehaviour,  # type: ignore
    }

    def setup(self) -> None:
        """Set up the behaviour."""
        super().setup()
        benchmark_tool.logger = self.context.logger
