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

"""This module contains the class to connect to a ERC20 contract."""
import logging

from aea.configurations.base import PublicId
from aea.contracts.base import Contract


PUBLIC_ID = PublicId.from_str("valory/uniswap_v2_erc20:0.1.0")

_logger = logging.getLogger(
    f"aea.packages.{PUBLIC_ID.author}.contracts.{PUBLIC_ID.name}.contract"
)


class UniswapV2ERC20(Contract):
    """The Uniswap V2 ERC-20 contract."""

    @classmethod
    def approve(cls, spender_address: int, value: int) -> bool:
        """Set the allowance."""
        return False

    @classmethod
    def transfer(cls, to_address: str, value: int) -> bool:
        """Transfer funds to an account."""
        return False

    @classmethod
    def transfer_from(cls, from_address: str, to_address: str, value: int) -> bool:
        """Transfer funds between accounts."""
        return False

    @classmethod
    def permit(
        cls,
        owner_address: str,
        spender_address: str,
        value: int,
        deadline: int,
        v: int,
        r: bytes,
        s: bytes,
    ) -> None:
        """Modify the allowance mapping using a signed message."""
        return None
