#!/usr/bin/env bash

URL=`curl -L https://quicksync.io/osmosis.json|jq -r '.[] |.url'`
cd $HOME/.osmosisd/
wget -O - $URL | lz4 -d | tar -xvf -
