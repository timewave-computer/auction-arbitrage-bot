# Local Interchain Test

This folder provides integration tests for verifying the correctness of the arb bot.

## Setup

In order to run the unit tests, please run the following commands:

Clone local-interchain:

```bash
git clone git@github.com:strangelove-ventures/interchaintest.git
```

Install `local-ic`:

```bash
cd interchaintest/local-interchain

make install
```

## Usage

Start `local-ic`:

```bash
local-ic start neutron_osmosis
```
