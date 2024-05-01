PROTO_DIR = 'proto'
PROTO_SOURCES := $(shell find $(PROTO_DIR) -name '*.proto')

build/gen:
	mkdir -p $@

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
