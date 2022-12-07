# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2022 Valory AG
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

"""Chain constants"""

import os

from autonomy.data import DATA_DIR


ABI_DIR = DATA_DIR / "abis"


COMPONENT_REGISTRY_ABI_FILENAME = "ComponentRegistry.json"
AGENT_REGISTRY_ABI_FILENAME = "AgentRegistry.json"
REGISTRIES_MANAGER_ABI_FILENAME = "RegistriesManager.json"
SERVICE_MANAGER_ABI_FILENAME = "ServiceManager.json"
SERVICE_REGISTRY_ABI_FILENAME = "ServiceRegistry.json"

# contract addresses from `valory/autonolas-registries` image
COMPONENT_REGISTRY_ADDRESS_LOCAL = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
AGENT_REGISTRY_ADDRESS_LOCAL = "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512"
REGISTRIES_MANAGER_ADDRESS_LOCAL = "0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0"
SERVICE_MANAGER_ADDRESS_LOCAL = "0x70e0bA845a1A0F2DA3359C97E0285013525FFC49"
SERVICE_REGISTRY_ADDRESS_LOCAL = "0x998abeb3E57409262aE5b751f60747921B33613E"


# contract addresses from `https://github.com/valory-xyz/autonolas-registries/blob/main/docs/mainnet_addresses.json` file
COMPONENT_REGISTRY_ADDRESS_ETHEREUM = "0x15bd56669F57192a97dF41A2aa8f4403e9491776"
AGENT_REGISTRY_ADDRESS_ETHEREUM = "0x2F1f7D38e4772884b88f3eCd8B6b9faCdC319112"
REGISTRIES_MANAGER_ADDRESS_ETHEREUM = "0x9eC9156dEF5C613B2a7D4c46C383F9B58DfcD6fE"
SERVICE_MANAGER_ADDRESS_ETHEREUM = "0x38b062d11CD7596Ab5aDFe4d0e9F0dC3218E5389"
SERVICE_REGISTRY_ADDRESS_ETHEREUM = "0x48b6af7B12C71f09e2fC8aF4855De4Ff54e775cA"

# contract addresses from goerli
COMPONENT_REGISTRY_ADDRESS_GOERLI = "0x7Fd1F4b764fA41d19fe3f63C85d12bf64d2bbf68"
AGENT_REGISTRY_ADDRESS_GOERLI = "0xEB5638eefE289691EcE01943f768EDBF96258a80"
REGISTRIES_MANAGER_ADDRESS_GOERLI = "0x10c5525F77F13b28f42c5626240c001c2D57CAd4"
SERVICE_MANAGER_ADDRESS_GOERLI = "0xcDdD9D9ABaB36fFa882530D69c73FeE5D4001C2d"
SERVICE_REGISTRY_ADDRESS_GOERLI = "0x1cEe30D08943EB58EFF84DD1AB44a6ee6FEff63a"

# contract addressed for a custom chain
COMPONENT_REGISTRY_ADDRESS_CUSTOM = os.environ.get("CUSTOM_COMPONENT_REGISTRY_ADDRESS")
AGENT_REGISTRY_ADDRESS_CUSTOM = os.environ.get("CUSTOM_AGENT_REGISTRY_ADDRESS")
REGISTRIES_MANAGER_ADDRESS_CUSTOM = os.environ.get("CUSTOM_REGISTRIES_MANAGER_ADDRESS")
SERVICE_MANAGER_ADDRESS_CUSTOM = os.environ.get("CUSTOM_SERVICE_MANAGER_ADDRESS")
SERVICE_REGISTRY_ADDRESS_CUSTOM = os.environ.get("CUSTOM_SERVICE_REGISTRY_ADDRESS")
