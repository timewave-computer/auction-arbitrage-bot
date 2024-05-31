PROTO_DIR = 'proto'
PROTO_SOURCES := $(shell find $(PROTO_DIR) -name '*.proto')

build/gen:
	mkdir -p $@

~/.neutrond/config/genesis.json:
	wget -O genesis.json https://raw.githubusercontent.com/neutron-org/mainnet-assets/main/neutron-1-genesis.json --inet4-only && mv genesis.json ~/.neutrond/config
	wget -O addrbook.json https://raw.githubusercontent.com/neutron-org/mainnet-assets/main/addrbook.json --inet4-only && mv addrbook.json ~/.neutrond/config

~/.osmosisd/config/genesis.json:
	wget -O genesis.json https://github.com/osmosis-labs/networks/raw/main/osmosis-1/genesis.json --inet4-only && mv genesis.json ~/.osmosisd/config

.PHONY: genesis
genesis: ~/.neutrond/config/genesis.json ~/.osmosisd/config/genesis.json

.PHONY: sync
sync:
	./scripts/sync_osmo.sh
	./scripts/sync_neutro

.PHONY: proto
proto: build/gen $(PROTO_SOURCES)
	protoc --proto_path=$(PROTO_DIR) --python_out=build/gen --mypy_out=build/gen --go-grpc_out=build/gen --mypy_grpc_out=build/gen $(PROTO_SOURCES)

.PHONY: test
test:
	PYTHONPATH=src:build/gen python -m pytest tests

.PHONY: run
run:
	PYTHONPATH=src:build/gen python main.py --max_hops 5 -n 30

.PHONY: lint
lint:
	MYPYPATH=src:build/gen mypy src --install-types --disallow-untyped-calls --disallow-untyped-defs --disallow-incomplete-defs --strict
	MYPYPATH=src:build/gen mypy tests --install-types --disallow-untyped-calls --disallow-untyped-defs --disallow-incomplete-defs --strict
	MYPYPATH=src:build/gen mypy main.py --install-types --disallow-untyped-calls --disallow-untyped-defs --disallow-incomplete-defs
	ruff check src
	ruff check tests
	ruff check main.py
	PYTHONPATH=src:build/gen python -m pylint src
	PYTHONPATH=src:build/gen python -m pylint tests
	PYTHONPATH=src:build/gen python -m pylint main.py

.PHONY: clean
clean:
	rm -rf build/gen
