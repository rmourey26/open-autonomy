# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2021-2022 Valory AG
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

"""This module contains the transaction payloads for common apps."""
from enum import Enum
from typing import Any, Dict, Optional

from packages.valory.skills.abstract_round_abci.base import BaseTxPayload


class TransactionType(Enum):
    """Enumeration of transaction types."""

    REGISTRATION = "registration"

    def __str__(self) -> str:
        """Get the string value of the transaction type."""
        return self.value


class RegistrationPayload(BaseTxPayload):
    """Represent a transaction payload of type 'registration'."""

    transaction_type = TransactionType.REGISTRATION

    def __init__(
        self, sender: str, initialisation: Optional[str] = None, **kwargs: Any
    ) -> None:
        """Initialize an 'select_keeper' transaction payload.

        :param sender: the sender (Ethereum) address
        :param initialisation: the initialisation data
        :param kwargs: the keyword arguments
        """
        self._initialisation = initialisation
        super().__init__(sender, **kwargs)

    @property
    def initialisation(self) -> Optional[str]:
        """Get the initialisation."""
        return self._initialisation

    @property
    def data(self) -> Dict:
        """Get the data."""
        return (
            dict(initialisation=self.initialisation)
            if self.initialisation is not None
            else {}
        )
