# auction-arbitrage-bot

An extensible arbitrage bot for trading against valence auctions and Astroport and Osmosis.

## Installation

### Nix

Use [Nix](https://github.com/NixOS/nix) to automatically install all required dependencies, or [manually](#manual) install dependencies.

Nix can be installed through the following steps on MacOS and Linux:

#### MacOS

```sh
curl -L https://nixos.org/nix/install | sh
```

#### Linux

```sh
curl -L https://nixos.org/nix/install | sh -s -- --daemon
```

After installing Nix, enter the abritrage bot dev environment by executing

```sh
nix develop --extra-experimental-features nix-command --extra-experimental-features flakes
```

in the repository root. Proceed to [Usage](#usage) to use the arbitrage bot.

### Manual

#### Dependencies

This tool requires the following to be installed:

* Python 3.11
* `protoc`
* `protoc-gen-grpc`
* `make`

#### Procedure

Installation requires Python 3.11.

To install the auction-arbitrage-bot, first create a virtual environment:

```sh
python -m venv venv
```

Then, activate the virtual environment:

```sh
source venv/bin/activate
```

Then, install the required dependencies:

```sh
pip install -r requirements.txt
```

Then, compile cosmos and osmosis protobuf stubs:

```sh
make proto
```

## Usage

The auction arbitrage bot can be run with only one required environment variable: `WALLET_MNEMONIC`. This environment variable specifies where to look for initial funds for arbitrage. All available flags are:

* `-f` (`--pool_file`): Specifies which pools to use for the Neutron Astroport and Osmosis pool providers, and which routes to use for the Scheduler. Can also be used to cache requests required to obtain pool information.
* `-p` (`--poll_interval`): Specifies how frequently the arbitrage strategy should be run (in seconds). The default value is `120`.
* `-d` (`--discovery_interval`): Specifies how frequently new arbitrage routes should be discovered (in seconds). The default value is `600`.
* `-nh` (`--hops`): Specifies the number of "hops" in a single arbitrage trade. The default value is `3`. Note that increasing this value increases the time for the discovery algorithm to run.
* `-r` (`--require_leg_types`): Specifies the required arbitrage pool types to require trades include. Possible values include: auction osmosis astroport. Multiple leg types may be required, separated by spaces.
* `-b` (`--base_denom`): Specifies the denom in which profits are denominated. The default value is the Neutron Noble USDC denom (`ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81`)
* `-pm` (`--profit_margin`): Specifies the quantity of the base denom that must be obtained from an arbitrage opportunity to consider it. The default value is `1000`.
* `-l` (`--log_file`): Specifies a file to write daemon logs to. If left unspecified, stdout will be used. Stdout and a log file cannot be used simultaneously.
* `-c` (`--net_config`): Specifies a path to a file containing RPC URL's to use for different networks.

The bot may be run by executing:

```sh
WALLET_MNEMONIC=<wallet_mnemonic> python main.py
```

View available flags by executing:

```sh
python main.py --help
```

### Commands

The bot may also be supplied a command (an argument with no hyphens). The available commands are as follows:

* `dump`: Loads all known pools, and writes discovered pools to a file, exiting immediately after. Results will be written in JSON format, following conventions outlined below.
  * Sample execution: `WALLET_MNEMONIC=<wallet_mnemonic> python main.py dump`
* `daemon`: Spawns the searcher event-loop as a long-running background "daemon" process, after initial setup. Exits the init script after spawning the daemon, leaving the daemon running in the background.
* `hist`: Shows summary statistics for the bot's execution, including a lot of all recently completed orders.
* `hist show <id>`: Shows execution logs for a particular order, as well as the execution plan for the order.

### Custom RPC's

Custom RPC providers may be specified with the `--net_config` flag. This flag specifies a path to a JSON file with the following format:

```json
{
  "neutron": {
    "http": ["https://neutron-rest.publicnode.com"],
	"grpc": ["grpc+https://neutron-grpc.publicnode.com:443"]
  },
  "osmosis": {
    "http": ["https://lcd.osmosis.zone"],
	"grpc": ["https://osmosis-rpc.publicnode.com:443"]
  }
}
```

### Custom Pools

Custom pools and routes may be provided by utilizing a "pool file" via the `--pool_file` flag. Using this flag will avoid external calls to obtain pool listings, and will result in calls to `Directory` class' `.pools` methods looking up available pools from this file. Furthermore, if routes are specified, no route discovery will be performed, and the routes provided will be used. An example pool file is as follows:

```json
{
  "pools": {
    "neutron_astroport": [
      {
	    "asset_a": {
	      "native_token": {
		    "denom": "<DENOM>"
		  }
	    },
	    "asset_b": {
	      "token": {
		    "contract_addr": "<DENOM>"
		 }
	    },
	    "address": "<ADDR>"
	  }
    ],
    "osmosis": [
      {
	    "asset_a": "<DENOM>",
	    "asset_b": "<DENOM>",
	    "pool_id": 1234,
		"address": "<ADDR>"
	  }
    ]
  },
  "auctions": [
    {
	  "asset_a": "<DENOM>",
	  "asset_b": "<DENOM>",
	  "address": "<ADDR>"
	}
  ],
  "routes": [
    [
	  {
	    "osmosis": {
		  "asset_a": "<DENOM>",
		  "asset_b": "<DENOM>",
		  "pool_id": 1234
		}
	  },
	  {
	    "neutron_astroport": {
	      "asset_a": {
	        "native_token": {
		      "denom": "<DENOM>"
		    }
          },
	      "asset_b": {
	        "token": {
		      "contract_addr": "<DENOM>"
		    }
	      },
	      "address": "<ADDR>"
		}
	  },
	  {
	    "auction": {
	      "asset_a": "<DENOM>",
	      "asset_b": "<DENOM>",
	      "address": "<ADDR>"
        }
	  }
	]
  ]
}
```

### Output logs

By default, the arbitrage bot logs to the INFO level the following:

* Attempted arbitrage trades
* Successful arbitrage trades
* Failing arbitrage trades

Debug information is logged to the DEBUG level, including:

* Discovered arbitrage trades
* Profit levels for considered trades
* Intermediate debug logs

The log level may be set via the `LOGLEVEL` environment variable. Possible values are: `INFO`, `DEBUG`, or `ERROR`.

An example output is as follows:

```
INFO:__main__:Building pool catalogue
INFO:__main__:Built pool catalogue with 2440 pools
INFO:src.strategies.naive:Finding profitable routes
INFO:src.strategies.naive:Executing route with profit of 43521870: astroport: factory/neutron154gg0wtm2v4h9ur8xg32ep64e8ef0g5twlsgvfeajqwghdryvyqsqhgk8e/APOLLO - ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81 -> osmosis: ibc/73BB20AF857D1FE6E061D01CA13870872AD0C979497CAF71BEA25B1CBF6879F1 - ibc/498A0751C798A0D9A389AA3691123DADA57DAA4FE165D5C75894505B876BA6E4 -> valence: ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81 - factory/neutron1p8d89wvxyjcnawmgw72klknr3lg9gwwl6ypxda/newt
INFO:src.strategies.naive:Queueing candidate arbitrage opportunity with route with 3 hop(s): astroport: factory/neutron154gg0wtm2v4h9ur8xg32ep64e8ef0g5twlsgvfeajqwghdryvyqsqhgk8e/APOLLO - ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81 -> osmosis: ibc/73BB20AF857D1FE6E061D01CA13870872AD0C979497CAF71BEA25B1CBF6879F1 - ibc/498A0751C798A0D9A389AA3691123DADA57DAA4FE165D5C75894505B876BA6E4 -> valence: ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81 - factory/neutron1p8d89wvxyjcnawmgw72klknr3lg9gwwl6ypxda/newt
INFO:src.strategies.naive:Queueing arb leg on astroport with factory/neutron154gg0wtm2v4h9ur8xg32ep64e8ef0g5twlsgvfeajqwghdryvyqsqhgk8e/APOLLO -> ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81
INFO:src.strategies.naive:Executing arb leg on astroport with 534486 ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81 -> factory/neutron154gg0wtm2v4h9ur8xg32ep64e8ef0g5twlsgvfeajqwghdryvyqsqhgk8e/APOLLO
INFO:src.strategies.naive:Arb leg can be executed atomically; no transfer necessary
INFO:src.strategies.naive:Submitting arb to contract: neutron15wal8wsy7mq37hagmrzchwmugpjzwlzrlw7pylkhlfuwukmc2kps722ems
INFO:src.strategies.naive:Executed leg ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81 -> factory/neutron154gg0wtm2v4h9ur8xg32ep64e8ef0g5twlsgvfeajqwghdryvyqsqhgk8e/APOLLO: 99D428AFC53499249A5FCA624F19273CCF7F886322621F79B12358830745739C
INFO:src.strategies.naive:Queueing arb leg on osmosis with ibc/73BB20AF857D1FE6E061D01CA13870872AD0C979497CAF71BEA25B1CBF6879F1 -> ibc/498A0751C798A0D9A389AA3691123DADA57DAA4FE165D5C75894505B876BA6E4
INFO:src.strategies.naive:Executing arb leg on osmosis with 4789772 ibc/73BB20AF857D1FE6E061D01CA13870872AD0C979497CAF71BEA25B1CBF6879F1 -> ibc/498A0751C798A0D9A389AA3691123DADA57DAA4FE165D5C75894505B876BA6E4
INFO:src.strategies.naive:Transfering 4789772 factory/neutron154gg0wtm2v4h9ur8xg32ep64e8ef0g5twlsgvfeajqwghdryvyqsqhgk8e/APOLLO from neutron-1 -> osmosis-1
INFO:src.strategies.naive:Submitted IBC transfer from src neutron-1 to osmosis-1: A4C71E50DC5CAE19BB12AB0F3F0417AAD86352AC60BFE51E2B270579EBDDC818
INFO:src.strategies.naive:Checking IBC transfer status A4C71E50DC5CAE19BB12AB0F3F0417AAD86352AC60BFE51E2B270579EBDDC818
INFO:src.strategies.naive:Transfer succeeded: neutron-1 -> osmosis-1
INFO:src.strategies.naive:Balance to swap for osmo179y4yx6m0r73r0qlrx5axt7m7d05xpp3urkzve on osmosis-1: 4789772
INFO:src.strategies.naive:Submitting arb to pool: 1410
INFO:src.strategies.naive:Executed leg ibc/73BB20AF857D1FE6E061D01CA13870872AD0C979497CAF71BEA25B1CBF6879F1 -> ibc/498A0751C798A0D9A389AA3691123DADA57DAA4FE165D5C75894505B876BA6E4: DBD4B87C02A2EFA89125A3D8DC47FB3B3802C066AD8FAAF70EE1F1F8236F6583
INFO:src.strategies.naive:Queueing arb leg on valence with ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81 -> factory/neutron1p8d89wvxyjcnawmgw72klknr3lg9gwwl6ypxda/newt
INFO:src.strategies.naive:Executing arb leg on valence with 513630 ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81 -> factory/neutron1p8d89wvxyjcnawmgw72klknr3lg9gwwl6ypxda/newt
INFO:src.strategies.naive:Transfering 513630 ibc/498A0751C798A0D9A389AA3691123DADA57DAA4FE165D5C75894505B876BA6E4 from osmosis-1 -> neutron-1
...
```
