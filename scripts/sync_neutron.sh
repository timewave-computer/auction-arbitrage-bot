#!/usr/bin/env bash
set -uxe

# Cosmovisor conf
export DAEMON_NAME=neutrond
export DAEMON_HOME=$HOME/.neutrond
export DAEMON_RESTART_AFTER_UPGRADE=true

mkdir -p $HOME/.neutrond/cosmovisor/genesis/bin
stat $HOME/.neutrond/cosmovisor/genesis/bin/neutrond || cp $(which neutrond) $HOME/.neutrond/cosmovisor/genesis/bin

# Get "trust_hash" and "trust_height".
INTERVAL=100
LATEST_HEIGHT=$(curl -s https://rpc-kralum.neutron-1.neutron.org/block | jq -r .result.block.header.height)
BLOCK_HEIGHT=$((LATEST_HEIGHT - INTERVAL))
TRUST_HASH=$(curl -s "https://rpc-kralum.neutron-1.neutron.org/block?height=$BLOCK_HEIGHT" | jq -r .result.block_id.hash)

# Print out block and transaction hash from which to sync state.
echo "trust_height: $BLOCK_HEIGHT"
echo "trust_hash: $TRUST_HASH"

# Export state sync variables.
export NEUTROND_STATESYNC_ENABLE=true
export NEUTROND_P2P_MAX_NUM_OUTBOUND_PEERS=500
export NEUTROND_STATESYNC_RPC_SERVERS="https://rpc-kralum.neutron-1.neutron.org:443,https://rpc-kralum.neutron-1.neutron.org:443"
export NEUTROND_STATESYNC_TRUST_HEIGHT=$BLOCK_HEIGHT
export NEUTROND_STATESYNC_TRUST_HASH=$TRUST_HASH
export NEUTROND_P2P_LADDR=tcp://0.0.0.0:7777
export NEUTROND_RPC_LADDR=tcp://127.0.0.1:7711
export NEUTROND_GRPC_ADDRESS=127.0.0.1:7712
export NEUTROND_GRPC_WEB_ADDRESS=127.0.0.1:8014
export NEUTROND_API_ADDRESS=tcp://127.0.0.1:8013
export NEUTROND_NODE=tcp://127.0.0.1:8011
export NEUTROND_P2P_MAX_NUM_INBOUND_PEERS=500
export NEUTROND_RPC_PPROF_LADDR=127.0.0.1:6969

# Fetch and set list of seeds from chain registry.
NEUTROND_P2P_SEEDS=$(curl -s https://raw.githubusercontent.com/cosmos/chain-registry/master/neutron/chain.json | jq -r '[foreach .peers.seeds[] as $item (""; "\($item.id)@\($item.address)")] | join(",")')
export NEUTROND_P2P_SEEDS

# Start chain.
cosmovisor run start --minimum-gas-prices 0.01untrn --x-crisis-skip-assert-invariants --iavl-disable-fastnode false
