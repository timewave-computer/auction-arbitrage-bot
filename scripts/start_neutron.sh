#!/usr/bin/env sh

neutrond start --minimum-gas-prices 0.0001untrn --rpc.laddr "tcp://127.0.0.1:26657" --grpc.enable true --grpc.address "localhost:9090" --grpc-web.enable true --grpc-web.address "localhost:9091" --api.enable true --api.address "tcp://localhost:1317"
