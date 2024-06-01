#!/usr/bin/env sh

osmosisd start --minimum-gas-prices 0.0001untrn --rpc.laddr "tcp://127.0.0.1:26647" --grpc.enable true --grpc.address "localhost:9080" --grpc-web.enable true --grpc-web.address "localhost:9081" --api.enable true --api.address "tcp://localhost:1307"
