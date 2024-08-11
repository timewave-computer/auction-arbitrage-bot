# Local Interchain Test

This directory provides integration tests for verifying the correctness of the arbitrage bot.

## Setup

In order to run the integration tests, please run the following commands:

### Clone local-interchain

```bash
git clone git@github.com:strangelove-ventures/interchaintest.git
```

### Install `local-ic`:

```bash
cd interchaintest/local-interchain

make install
```

## Usage

### Start `local-ic`:

```bash
local-ic start neutron_osmosis_gaia --api-port 42069
```

### Run tests

```bash
make test
```
