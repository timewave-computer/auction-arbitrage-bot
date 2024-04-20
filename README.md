# auction-arbitrage-bot

An extensible arbitrage bot for trading against valence auctions and Astroport and Osmosis.

TODO: Add idea about threading
TODO: Track which pools have been touched to inform route samping, or get a price feed from coingecko, and just filter for the coins that are relevant to the DEX you're looking at and query for large price movements, and you can pick the top 10 to inform route collection
TODO: Look at Yan's contract and do backtesting (95% of blocks)
TODO: Trailing 7 days simulated number of auctions

## Installation

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

## Usage

The auction arbitrage bot can be run with only one required flag: `wallet_address`. This flag specifies where to look for initial funds for arbitrage. All available flags are:

* `-f` (`--pool_file`): Specifies which pools to use for the Neutron Astroport and Osmosis pool providers, and which routes to use for the Scheduler. Can also be used to cache requests required to obtain pool information.
* `-p` (`--poll_interval`): Specifies how frequently the arbitrage strategy should be run (in seconds). The default value is `120`.
* `-d` (`--discovery_interval`): Specifies how frequently new arbitrage routes should be discovered (in seconds). The default value is `600`.
* `-m` (`--max_hops`): Specifies the maximum number of "hops" in a single arbitrage trade. The default value is `3`. Note that increasing this value increases the time for the discovery algorithm to run.
* `n` (`--num_routes_considered`): Specifies the number of routes to discover. The default value is `30`. Note that increasing this value increases the time for the discovery algorithm to run.
* `-b` (`--base_denom`): Specifies the denom in which profits are denominated. The default value is the Neutron Noble USDC denom (`ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81`)
* `-pm` (`--profit_margin`): Specifies the quantity of the base denom that must be obtained from an arbitrage opportunity to consider it. The default value is `10`.
* `-w` (`--wallet_address`): Specifies the address of the wallet from which funds should be used to execute trades. This flag is **required**.

The bot may be run by executing:

```sh
python main.py --wallet_address <WALLET_ADDRESS>
```

### Commands

The bot may also be supplied a command (an argument with no hyphens). The available commands are as follows:

* `dump`: Explores all known pools with the given `--max_hops` and `--limit`, and writes discovered routes to a file, exiting immediately after. Results will be written in JSON format, following conventions outlined below.
  * Sample execution: `python main.py --wallet_address <WALLET_ADDRESS> dump`

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
	    "asset_b": "<DENOM>,
	    "pool_id": 1234
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

By default, the arbitrage bot logs:

* Discovered routes
* Considered routes

An example output is as follows:

```
INFO:__main__:Building pool catalogue
INFO:__main__:Built pool catalogue with 2274 pools
INFO:src.strategies.naive:Building route tree from ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81 with 4560 vertices (this may take a while)
INFO:src.strategies.naive:Closed circuit from ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81 to ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81; registering route
INFO:src.strategies.naive:Discovered route with 3 hop(s): ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81 - ibc/2CB87BCE0937B1D1DFCEE79BE4501AAF3C265E923509AEAC410AD85D27F35130 -> ibc/F082B65C88E4B6D5EF1DB243CDA1D331D002759E938A0F5CD3FFDC5D53B3E349 - ibc/2CB87BCE0937B1D1DFCEE79BE4501AAF3C265E923509AEAC410AD85D27F35130 -> ibc/F082B65C88E4B6D5EF1DB243CDA1D331D002759E938A0F5CD3FFDC5D53B3E349 - ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81
INFO:src.strategies.naive:Closed circuit from ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81 to ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81; registering route
INFO:src.strategies.naive:Discovered route with 3 hop(s): ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81 - ibc/2CB87BCE0937B1D1DFCEE79BE4501AAF3C265E923509AEAC410AD85D27F35130 -> ibc/F082B65C88E4B6D5EF1DB243CDA1D331D002759E938A0F5CD3FFDC5D53B3E349 - ibc/2CB87BCE0937B1D1DFCEE79BE4501AAF3C265E923509AEAC410AD85D27F35130 -> ibc/F082B65C88E4B6D5EF1DB243CDA1D331D002759E938A0F5CD3FFDC5D53B3E349 - ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81
...
INFO:src.strategies.naive:Finished building route tree; discovered 30 routes
INFO:src.strategies.naive:Found 6 profitable routes, with max profit of 139782323 and min profit of 6223766
INFO:src.strategies.naive:Candidate arbitrage opportunity #1 with profit of 6223766 and route with 3 hop(s): astroport: factory/neutron154gg0wtm2v4h9ur8xg32ep64e8ef0g5twlsgvfeajqwghdryvyqsqhgk8e/APOLLO - ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81 -> astroport: factory/neutron154gg0wtm2v4h9ur8xg32ep64e8ef0g5twlsgvfeajqwghdryvyqsqhgk8e/APOLLO - ibc/F082B65C88E4B6D5EF1DB243CDA1D331D002759E938A0F5CD3FFDC5D53B3E349 -> astroport: ibc/F082B65C88E4B6D5EF1DB243CDA1D331D002759E938A0F5CD3FFDC5D53B3E349 - ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81
INFO:src.strategies.naive:Candidate arbitrage opportunity #2 with profit of 6223766 and route with 3 hop(s): astroport: factory/neutron154gg0wtm2v4h9ur8xg32ep64e8ef0g5twlsgvfeajqwghdryvyqsqhgk8e/APOLLO - ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81 -> astroport: factory/neutron154gg0wtm2v4h9ur8xg32ep64e8ef0g5twlsgvfeajqwghdryvyqsqhgk8e/APOLLO - ibc/F082B65C88E4B6D5EF1DB243CDA1D331D002759E938A0F5CD3FFDC5D53B3E349 -> astroport: ibc/F082B65C88E4B6D5EF1DB243CDA1D331D002759E938A0F5CD3FFDC5D53B3E349 - ibc/B559A80D62249C8AA07A380E2A2BEA6E5CA9A6F079C912C3A9E9B494105E4F81
...
```
