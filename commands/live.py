# QUANTCONNECT.COM - Democratizing Finance, Empowering Individuals.
# Lean CLI v1.0. Copyright 2021 QuantConnect Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from email.policy import default
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import click

from lean.click import LeanCommand, PathParameter, ensure_options
from lean.constants import DEFAULT_ENGINE_IMAGE, GUI_PRODUCT_INSTALL_ID
from lean.container import container
from lean.models.brokerages.local import all_local_brokerages, local_brokerage_data_feeds, all_local_data_feeds
from lean.models.errors import MoreInfoError
from lean.models.logger import Option

# Brokerage -> required configuration properties
_required_brokerage_properties = {
    "InteractiveBrokersBrokerage": ["ib-account", "ib-user-name", "ib-password",
                                    "ib-agent-description", "ib-trading-mode"],
    "TradierBrokerage": ["tradier-use-sandbox", "tradier-account-id", "tradier-access-token"],
    "OandaBrokerage": ["oanda-environment", "oanda-access-token", "oanda-account-id"],
    "GDAXBrokerage": ["gdax-api-secret", "gdax-api-key", "gdax-passphrase"],
    "BitfinexBrokerage": ["bitfinex-api-secret", "bitfinex-api-key"],
    "BinanceBrokerage": ["binance-api-secret", "binance-api-key"],
    "ZerodhaBrokerage": ["zerodha-access-token", "zerodha-api-key", "zerodha-product-type", "zerodha-trading-segment"],
    "SamcoBrokerage": ["samco-client-id", "samco-client-password", "samco-year-of-birth", "samco-product-type", "samco-trading-segment"],
    "BloombergBrokerage": ["job-organization-id", "bloomberg-api-type", "bloomberg-environment",
                           "bloomberg-server-host", "bloomberg-server-port", "bloomberg-emsx-broker"],
    "AtreyuBrokerage": ["job-organization-id", "atreyu-host", "atreyu-req-port", "atreyu-sub-port",
                        "atreyu-username", "atreyu-password",
                        "atreyu-client-id", "atreyu-broker-mpid", "atreyu-locate-rqd"],
    "TradingTechnologiesBrokerage": ["job-organization-id", "tt-user-name", "tt-session-password", "tt-account-name",
                                     "tt-rest-app-key", "tt-rest-app-secret", "tt-rest-environment",
                                     "tt-market-data-sender-comp-id", "tt-market-data-target-comp-id",
                                     "tt-market-data-host", "tt-market-data-port",
                                     "tt-order-routing-sender-comp-id", "tt-order-routing-target-comp-id",
                                     "tt-order-routing-host", "tt-order-routing-port",
                                     "tt-log-fix-messages"],
    "KrakenBrokerage": ["kraken-api-key", "kraken-api-secret", "kraken-verification-tier"],
    "FTXBrokerage": ["ftx-api-key", "ftx-api-secret", "ftx-account-tier", "ftx-exchange-name"]
}

# Data queue handler -> required configuration properties
_required_data_queue_handler_properties = {
    "InteractiveBrokersBrokerage":
        _required_brokerage_properties["InteractiveBrokersBrokerage"] + ["ib-enable-delayed-streaming-data"],
    "TradierBrokerage": _required_brokerage_properties["TradierBrokerage"],
    "OandaBrokerage": _required_brokerage_properties["OandaBrokerage"],
    "GDAXDataQueueHandler": _required_brokerage_properties["GDAXBrokerage"],
    "BitfinexBrokerage": _required_brokerage_properties["BitfinexBrokerage"],
    "BinanceBrokerage": _required_brokerage_properties["BinanceBrokerage"],
    "ZerodhaBrokerage": _required_brokerage_properties["ZerodhaBrokerage"] + ["zerodha-history-subscription"],
    "SamcoBrokerage": _required_brokerage_properties["SamcoBrokerage"],
    "BloombergBrokerage": _required_brokerage_properties["BloombergBrokerage"],
    "TradingTechnologiesBrokerage": _required_brokerage_properties["TradingTechnologiesBrokerage"],
    "QuantConnect.ToolBox.IQFeed.IQFeedDataQueueHandler": ["iqfeed-iqconnect", "iqfeed-productName", "iqfeed-version"],
    "KrakenBrokerage": _required_brokerage_properties["KrakenBrokerage"],
    "FTXBrokerage": _required_brokerage_properties["FTXBrokerage"]
}

_environment_skeleton = {
    "live-mode": True,
    "setup-handler": "QuantConnect.Lean.Engine.Setup.BrokerageSetupHandler",
    "result-handler": "QuantConnect.Lean.Engine.Results.LiveTradingResultHandler",
    "data-feed-handler": "QuantConnect.Lean.Engine.DataFeeds.LiveTradingDataFeed",
    "real-time-handler": "QuantConnect.Lean.Engine.RealTime.LiveTradingRealTimeHandler"
}


def _raise_for_missing_properties(lean_config: Dict[str, Any], environment_name: str, lean_config_path: Path) -> None:
    """Raises an error if any required properties are missing.

    :param lean_config: the LEAN configuration that should be used
    :param environment_name: the name of the environment
    :param lean_config_path: the path to the LEAN configuration file
    """
    environment = lean_config["environments"][environment_name]
    for key in ["live-mode-brokerage", "data-queue-handler"]:
        if key not in environment:
            raise MoreInfoError(f"The '{environment_name}' environment does not specify a {key}",
                                "https://www.lean.io/docs/lean-cli/live-trading")

    brokerage = environment["live-mode-brokerage"]
    data_queue_handler = environment["data-queue-handler"]

    brokerage_properties = _required_brokerage_properties.get(brokerage, [])
    data_queue_handler_properties = _required_data_queue_handler_properties.get(data_queue_handler, [])

    required_properties = brokerage_properties + data_queue_handler_properties
    missing_properties = [p for p in required_properties if p not in lean_config or lean_config[p] == ""]
    missing_properties = set(missing_properties)
    if len(missing_properties) == 0:
        return

    properties_str = "properties" if len(missing_properties) > 1 else "property"
    these_str = "these" if len(missing_properties) > 1 else "this"

    missing_properties = "\n".join(f"- {p}" for p in missing_properties)

    raise RuntimeError(f"""
Please configure the following missing {properties_str} in {lean_config_path}:
{missing_properties}
Go to the following url for documentation on {these_str} {properties_str}:
https://www.lean.io/docs/lean-cli/live-trading
    """.strip())


def _start_iqconnect_if_necessary(lean_config: Dict[str, Any], environment_name: str) -> None:
    """Starts IQConnect if the given environment uses IQFeed as data queue handler.

    :param lean_config: the LEAN configuration that should be used
    :param environment_name: the name of the environment
    """
    environment = lean_config["environments"][environment_name]
    if environment["data-queue-handler"] != "QuantConnect.ToolBox.IQFeed.IQFeedDataQueueHandler":
        return

    args = [lean_config["iqfeed-iqconnect"],
            "-product", lean_config["iqfeed-productName"],
            "-version", lean_config["iqfeed-version"]]

    username = lean_config.get("iqfeed-username", "")
    if username != "":
        args.extend(["-login", username])

    password = lean_config.get("iqfeed-password", "")
    if password != "":
        args.extend(["-password", password])

    subprocess.Popen(args)

    container.logger().info("Waiting 10 seconds for IQFeed to start")
    time.sleep(10)


def _configure_lean_config_interactively(lean_config: Dict[str, Any], environment_name: str) -> None:
    """Interactively configures the Lean config to use.

    Asks the user all questions required to set up the Lean config for local live trading.

    :param lean_config: the base lean config to use
    :param environment_name: the name of the environment to configure
    """
    logger = container.logger()

    lean_config["environments"] = {
        environment_name: _environment_skeleton
    }

    brokerage = logger.prompt_list("Select a brokerage", [
        Option(id=brokerage, label=brokerage.get_name()) for brokerage in all_local_brokerages
    ])

    brokerage.build(lean_config, logger).configure(lean_config, environment_name)

    data_feed = logger.prompt_list("Select a data feed", [
        Option(id=data_feed, label=data_feed.get_name()) for data_feed in local_brokerage_data_feeds[brokerage]
    ])
    is_data_feed_brokerage = True if brokerage._name == data_feed._name else False
    data_feed.build(lean_config, logger, is_data_feed_brokerage).configure(lean_config, environment_name)


_cached_organizations = None


def _get_organization_id(given_input: Optional[str], label: str) -> str:
    """Converts the organization name or id given by the user to an organization id.

    Shows an interactive wizard if no input is given.

    Raises an error if the user is not a member of an organization with the given name or id.

    :param given_input: the input given by the user
    :param label: the name of the module the organization id is needed for
    :return: the id of the organization given by the user
    """
    global _cached_organizations
    if _cached_organizations is None:
        api_client = container.api_client()
        _cached_organizations = api_client.organizations.get_all()

    if given_input is not None:
        organization = next((o for o in _cached_organizations if o.id == given_input or o.name == given_input), None)
        if organization is None:
            raise RuntimeError(f"You are not a member of an organization with name or id '{given_input}'")
    else:
        logger = container.logger()
        options = [Option(id=organization, label=organization.name) for organization in _cached_organizations]
        organization = logger.prompt_list(f"Select the organization with the {label} module subscription", options)

    return organization.id


_cached_lean_config = None


def _get_default_value(key: str) -> Optional[Any]:
    """Returns the default value for an option based on the Lean config.

    :param key: the name of the property in the Lean config that supplies the default value of an option
    :return: the value of the property in the Lean config, or None if there is none
    """
    global _cached_lean_config
    if _cached_lean_config is None:
        _cached_lean_config = container.lean_config_manager().get_lean_config()

    if key not in _cached_lean_config:
        return None

    value = _cached_lean_config[key]
    if value == "":
        return None

    if key == "iqfeed-iqconnect" and not Path(value).is_file():
        return None

    return value
        
def options_from_db(options):
    map_to_types = dict(
        array=str,
        number=float,
        string=str,
    )
    def decorator(f):
        for opt_params in reversed(options):
            param_decls = (
                '--' + opt_params['long'],
                opt_params['Name'])
            option_type = 'ronit'
            attrs = dict(
                type=map_to_types.get(opt_params['type'], opt_params['type']),
                help=opt_params['Help'],
                default=_get_default_value(opt_params['long'])
            )

            click.option(*param_decls, **attrs)(f)
        return f
    return decorator

run_options = [
    {
        "Name": "binance_api_key",
        "long": "binance-api-key",
        "Help": "what is this",
        "type": "string",
        "required": False
    }
]


@click.command(cls=LeanCommand, requires_lean_config=True, requires_docker=True)
@click.argument("project", type=PathParameter(exists=True, file_okay=True, dir_okay=True))
@options_from_db(run_options)
def live(project: Path, *args, **kwargs) -> None:
    """Start live trading a project locally using Docker.

    \b
    If PROJECT is a directory, the algorithm in the main.py or Main.cs file inside it will be executed.
    If PROJECT is a file, the algorithm in the specified file will be executed.

    By default an interactive wizard is shown letting you configure the brokerage and data feed to use.
    If --environment, --brokerage or --data-feed are given the command runs in non-interactive mode.
    In this mode the CLI does not prompt for input.

    If --environment is given it must be the name of a live environment in the Lean configuration.

    If --brokerage and --data-feed are given, the options specific to the given brokerage/data feed must also be given.
    The Lean config is used as fallback when a brokerage/data feed-specific option hasn't been passed in.
    If a required option is not given and cannot be found in the Lean config the command aborts.

    By default the official LEAN engine image is used.
    You can override this using the --image option.
    Alternatively you can set the default engine image for all commands using `lean config set engine-image <image>`.
    """
    # Reset globals so we reload everything in between tests
    global _cached_organizations
    _cached_organizations = None
    global _cached_lean_config
    _cached_lean_config = None

    project_manager = container.project_manager()
    algorithm_file = project_manager.find_algorithm_file(Path(project))

    if output is None:
        output = algorithm_file.parent / "live" / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    if kwargs["gui"]:
        module_manager = container.module_manager()
        module_manager.install_module(GUI_PRODUCT_INSTALL_ID, _get_organization_id(gui_organization, "local GUI"))

        detach = True

    lean_config_manager = container.lean_config_manager()

    if environment is not None and (brokerage is not None or data_feed is not None):
        raise RuntimeError("--environment and --brokerage + --data-feed are mutually exclusive")

    if environment is not None:
        environment_name = environment
        lean_config = lean_config_manager.get_complete_lean_config(environment_name, algorithm_file, None)
    elif brokerage is not None or data_feed is not None:
        ensure_options(["brokerage", "data_feed"])

        brokerage_configurer = [local_brokerage for local_brokerage in all_local_brokerages if local_brokerage.get_name()][0]
        ensure_options(brokerage_configurer.get_required_properties())
        brokerage_configurer.update_properties()
        
        data_feed_configurer = [local_data_feed for local_data_feed in all_local_data_feeds if local_data_feed.get_name()][0]
        ensure_options(data_feed_configurer.get_required_properties())
        data_feed_configurer.update_properties()

        environment_name = "lean-cli"
        lean_config = lean_config_manager.get_complete_lean_config(environment_name, algorithm_file, None)

        lean_config["environments"] = {
            environment_name: _environment_skeleton
        }

        brokerage_configurer.configure(lean_config, environment_name)
        data_feed_configurer.configure(lean_config, environment_name)
    else:
        environment_name = "lean-cli"
        lean_config = lean_config_manager.get_complete_lean_config(environment_name, algorithm_file, None)
        _configure_lean_config_interactively(lean_config, environment_name)

    if "environments" not in lean_config or environment_name not in lean_config["environments"]:
        lean_config_path = lean_config_manager.get_lean_config_path()
        raise MoreInfoError(f"{lean_config_path} does not contain an environment named '{environment_name}'",
                            "https://www.lean.io/docs/lean-cli/live-trading")

    if not lean_config["environments"][environment_name]["live-mode"]:
        raise MoreInfoError(f"The '{environment_name}' is not a live trading environment (live-mode is set to false)",
                            "https://www.lean.io/docs/lean-cli/live-trading")

    _raise_for_missing_properties(lean_config, environment_name, lean_config_manager.get_lean_config_path())

    project_config_manager = container.project_config_manager()
    cli_config_manager = container.cli_config_manager()

    project_config = project_config_manager.get_project_config(algorithm_file.parent)
    engine_image = cli_config_manager.get_engine_image(image or project_config.get("engine-image", None))

    container.update_manager().pull_docker_image_if_necessary(engine_image, update)

    _start_iqconnect_if_necessary(lean_config, environment_name)

    if not output.exists():
        output.mkdir(parents=True)

    output_config_manager = container.output_config_manager()
    lean_config["algorithm-id"] = f"L-{output_config_manager.get_live_deployment_id(output)}"

    if gui:
        lean_config["lean-manager-type"] = "QuantConnect.GUI.GuiLeanManager"
        output_config_manager.get_output_config(output).set("gui", True)

    lean_runner = container.lean_runner()
    lean_runner.run_lean(lean_config, environment_name, algorithm_file, output, engine_image, None, release, detach)

    if gui:
        logger = container.logger()
        logger.info(f"You can monitor the status of the live deployment in the GUI")
