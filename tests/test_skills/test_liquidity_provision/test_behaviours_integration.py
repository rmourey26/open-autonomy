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
"""Tests for valory/liquidity_provision skill behaviours with Hardhat."""
import asyncio
from pathlib import Path
from threading import Thread
from typing import Any, Dict, Optional, cast

from aea.helpers.transaction.base import RawTransaction
from aea.mail.base import Envelope, Message
from aea.multiplexer import Multiplexer
from aea.skills.base import Handler

from packages.open_aea.protocols.signing import SigningMessage
from packages.valory.protocols.contract_api.message import ContractApiMessage
from packages.valory.skills.abstract_round_abci.behaviour_utils import BaseState
from packages.valory.skills.liquidity_provision.behaviours import (
    EnterPoolTransactionHashBehaviour,
    EnterPoolTransactionSendBehaviour,
    ExitPoolTransactionHashBehaviour,
    get_strategy_update,
)
from packages.valory.skills.liquidity_provision.handlers import (
    ContractApiHandler,
    SigningHandler,
)
from packages.valory.skills.liquidity_provision.rounds import PeriodState

from tests.conftest import ROOT_DIR, make_ledger_api_connection
from tests.fixture_helpers import HardHatAMMBaseTest
from tests.helpers.contracts import get_register_contract
from tests.test_skills.test_liquidity_provision.test_behaviours import (
    LiquidityProvisionBehaviourBaseCase,
)
from tests.test_skills.test_price_estimation_abci.test_rounds import (
    get_participant_to_signature,
)


DEFAULT_GAS = 1000000
DEFAULT_GAS_PRICE = 1000000


class TestEnterPoolTransactionHashBehaviourHardhat(
    LiquidityProvisionBehaviourBaseCase, HardHatAMMBaseTest
):
    """Test liquidity pool behaviours in a Hardhat environment."""

    running_loop: asyncio.AbstractEventLoop
    thread_loop: Thread
    multiplexer: Multiplexer

    @classmethod
    def _setup_class(cls, **kwargs: Any) -> None:
        """Setup class."""
        pass

    @classmethod
    def setup(cls, **kwargs: Any) -> None:
        """Setup."""
        super().setup()
        # register all contracts we need
        directory = Path(
            ROOT_DIR, "packages", "valory", "contracts", "uniswap_v2_router_02"
        )
        _ = get_register_contract(directory)

        directory = Path(
            ROOT_DIR, "packages", "valory", "contracts", "uniswap_v2_erc20"
        )
        _ = get_register_contract(directory)

        directory = Path(ROOT_DIR, "packages", "valory", "contracts", "multisend")
        _ = get_register_contract(directory)

        directory = Path(ROOT_DIR, "packages", "valory", "contracts", "gnosis_safe")
        _ = get_register_contract(directory)
        # setup a multiplexer with the required connections
        cls.running_loop = asyncio.new_event_loop()
        cls.thread_loop = Thread(target=cls.running_loop.run_forever)
        cls.thread_loop.start()
        cls.multiplexer = Multiplexer(
            [make_ledger_api_connection()], loop=cls.running_loop
        )
        cls.multiplexer.connect()

        cls.strategy = get_strategy_update()
        cls.default_period_state = PeriodState(
            most_voted_tx_hash="most_voted_tx_hash".encode().hex(),
            safe_contract_address="0x5FbDB2315678afecb367f032d93F642f64180aa3",
            most_voted_keeper_address="test_agent_address",
            most_voted_strategy=cls.strategy,
            multisend_contract_address="0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512",
            router_contract_address="0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9",
        )

    @classmethod
    def teardown(cls) -> None:
        """Tear down the multiplexer."""
        super().teardown()
        cls.multiplexer.disconnect()
        cls.running_loop.call_soon_threadsafe(cls.running_loop.stop)
        cls.thread_loop.join()

    def process_message_cycle(
        self,
        handler: Optional[Handler] = None,
        expected_content: Optional[Dict] = None,
        expected_types: Optional[Dict] = None,
    ) -> None:
        """
        Processes one request-response type message cycle.

        Steps:
        1. Calls act on behaviour to generate outgoing message
        2. Checks for message in outbox
        3. Sends message to multiplexer and waits for response.
        4. Passes message to handler
        5. Calls act on behaviour to process incoming message

        :param handler: the handler to handle a potential incoming message
        :param expected_content: the content to be expected
        :param expected_types: the types to be expected
        """
        self.liquidity_provision_behaviour.act_wrapper()

        message = None

        if type(handler) == SigningHandler:
            self.assert_quantity_in_decision_making_queue(1)
            message = self.get_message_from_decision_maker_inbox()
            assert message is not None, "No message in outbox."

        else:
            self.assert_quantity_in_outbox(1)
            message = self.get_message_from_outbox()
            assert message is not None, "No message in outbox."
            self.multiplexer.put(
                Envelope(
                    to=message.to,
                    sender=message.sender,
                    message=message,
                    context=None,
                )
            )

        if handler is not None:  # here signing handler gets stuck
            envelope = self.multiplexer.get(block=True)
            assert envelope is not None, "No envelope"
            incoming_message = envelope.message
            assert isinstance(incoming_message, Message)

            if expected_content is not None:
                assert all(
                    [
                        incoming_message._body.get(key, None) == value
                        for key, value in expected_content.items()
                    ]
                ), f"Actual content: {incoming_message._body}, expected: {expected_content}"

            if expected_types is not None:
                assert all(
                    [
                        type(incoming_message._body.get(key, None)) == value_type
                        for key, value_type in expected_types.items()
                    ]
                ), "Content type mismatch"

            handler.handle(incoming_message)
        self.liquidity_provision_behaviour.act_wrapper()

    def process_n_messsages(
        self,
        state_id: str,
        ncycles: int,
        handler: Optional[Handler] = None,
        period_state: Optional[PeriodState] = None,
    ) -> None:
        """
        Process n message cycles.

        :param: state_id: the behaviour to fast forward to
        :param: ncycles: the number of message cycles to process
        :param: handler: a handler
        :param: period_state: a period_state
        """

        self.fast_forward_to_state(
            behaviour=self.liquidity_provision_behaviour,
            state_id=state_id,
            period_state=period_state if period_state else self.default_period_state,
        )
        assert (
            cast(
                BaseState,
                cast(BaseState, self.liquidity_provision_behaviour.current_state),
            ).state_id
            == state_id
        )

        expected_content = None
        expected_types = None

        if type(handler) == ContractApiHandler:
            expected_content = {
                "performative": ContractApiMessage.Performative.RAW_TRANSACTION
            }
            expected_types = {
                "dialogue_reference": tuple,
                "message_id": int,
                "raw_transaction": RawTransaction,
                "target": int,
            }

        elif type(handler) == SigningHandler:
            expected_content = {
                "performative": SigningMessage.Performative.SIGNED_MESSAGE
            }

        for _ in range(ncycles):
            self.process_message_cycle(handler, expected_content, expected_types)

        self.mock_a2a_transaction()

    # Safe behaviours

    def test_deploy_safe__send_behaviour(self) -> None:
        """test_deploy_safe_behaviour"""

    def test_deploy_safe_validation_behaviour(self) -> None:
        """test_deploy_safe_validation_behaviour"""

    # Enter pool behaviours

    def test_enter_pool_tx_hash_behaviour(self) -> None:
        """test_enter_pool_tx_hash_behaviour"""
        self.process_n_messsages(
            EnterPoolTransactionHashBehaviour.state_id, 7, self.contract_handler
        )

    def test_enter_pool_tx_sign_behaviour(self) -> None:
        """test_enter_pool_tx_sign_behaviour"""

    def test_enter_pool_tx_send_behaviour(self) -> None:
        """test_enter_pool_tx_send_behaviour"""

        multisend_data = "0x8d80ff0a0000000000000000000000000000000000000000000000000000000000000020000000000000000000000000000000000000000000000000000000000000053d00e7f1725e7734ce288f8367e1bb143e90bb3f05120000000000000000000000000000000000000000000000000000000000000001000000000000000000000000000000000000000000000000000000000000010438ed17390000000000000000000000000000000000000000000000000000000000000001000000000000000000000000000000000000000000000000000000000000000100000000000000000000000000000000000000000000000000000000000000a00000000000000000000000005fbdb2315678afecb367f032d93f642f64180aa3000000000000000000000000000000000000000000000000000000000000012c0000000000000000000000000000000000000000000000000000000000000002000000000000000000000000e7f1725e7734ce288f8367e1bb143e90bb3f0512000000000000000000000000dc64a140aa3e981100a9beca4e685f962f0cf6c900e7f1725e7734ce288f8367e1bb143e90bb3f05120000000000000000000000000000000000000000000000000000000000000001000000000000000000000000000000000000000000000000000000000000010438ed17390000000000000000000000000000000000000000000000000000000000000001000000000000000000000000000000000000000000000000000000000000000100000000000000000000000000000000000000000000000000000000000000a00000000000000000000000005fbdb2315678afecb367f032d93f642f64180aa3000000000000000000000000000000000000000000000000000000000000012c0000000000000000000000000000000000000000000000000000000000000002000000000000000000000000e7f1725e7734ce288f8367e1bb143e90bb3f05120000000000000000000000005fc8d32690cc91d4c39d9d3abcbd16989f87570700e7f1725e7734ce288f8367e1bb143e90bb3f051200000000000000000000000000000000000000000000000000000000000000010000000000000000000000000000000000000000000000000000000000000044095ea7b3000000000000000000000000cf7ed3acca5a467e9e704c703e8d87f634fb0fc9ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff00e7f1725e7734ce288f8367e1bb143e90bb3f051200000000000000000000000000000000000000000000000000000000000000010000000000000000000000000000000000000000000000000000000000000044095ea7b3000000000000000000000000cf7ed3acca5a467e9e704c703e8d87f634fb0fc9ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff00e7f1725e7734ce288f8367e1bb143e90bb3f051200000000000000000000000000000000000000000000000000000000000000010000000000000000000000000000000000000000000000000000000000000104e8e33700000000000000000000000000dc64a140aa3e981100a9beca4e685f962f0cf6c90000000000000000000000005fc8d32690cc91d4c39d9d3abcbd16989f87570700000000000000000000000000000000000000000000000000000000000000010000000000000000000000000000000000000000000000000000000000000001000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000005fbdb2315678afecb367f032d93f642f64180aa3000000000000000000000000000000000000000000000000000000000000012c000000"
        most_voted_tx_hash = (
            "0x26facffc4f345f169e0439bc74575a5cede9de65dccb0b6fd9e3433b1ea506d1"
        )
        keeper_address = "0xBcd4042DE499D14e55001CcbB24a551F3b954096"
        safe_contract_address = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
        multisend_contract_address = "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512"
        router_contract_address = "0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9"

        self._skill._skill_context._agent_context._identity._address = keeper_address
        participants = frozenset({keeper_address, "a_1", "a_2"})

        period_state = PeriodState(
            most_voted_tx_hash=most_voted_tx_hash.encode().hex(),
            most_voted_tx_data=str.encode(multisend_data),
            safe_contract_address=safe_contract_address,
            most_voted_keeper_address=keeper_address,
            most_voted_strategy=self.strategy,
            multisend_contract_address=multisend_contract_address,
            router_contract_address=router_contract_address,
            participants=participants,
            participant_to_signature=get_participant_to_signature(participants),
        )

        self.process_n_messsages(
            EnterPoolTransactionSendBehaviour.state_id,
            1,
            self.contract_handler,
            period_state,
        )
        self.mock_a2a_transaction()

    def test_enter_pool_tx_validation_behaviour(self) -> None:
        """test_enter_pool_tx_validation_behaviour"""

    # Exit pool behaviours

    def test_exit_pool_tx_hash_behaviour(self) -> None:
        """test_exit_pool_tx_hash_behaviour"""
        self.process_n_messsages(
            ExitPoolTransactionHashBehaviour.state_id, 7, self.contract_handler
        )

    def test_exit_pool_tx_sign_behaviour(self) -> None:
        """test_exit_pool_tx_sign_behaviour"""

    def test_exit_pool_tx_send_behaviour(self) -> None:
        """test_exit_pool_tx_send_behaviour"""

    def test_exit_pool_tx_validation_behaviour(self) -> None:
        """test_exit_pool_tx_validation_behaviour"""
