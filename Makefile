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
	PYTHONPATH=src:build/gen python main.py

.PHONY: lint
lint:
	mypy src --install-types --disallow-untyped-calls --disallow-untyped-defs --disallow-incomplete-defs --strict
	mypy tests --install-types --disallow-untyped-calls --disallow-untyped-defs --disallow-incomplete-defs --strict
	mypy main.py --install-types --disallow-untyped-calls --disallow-untyped-defs --disallow-incomplete-defs
	ruff check src
	ruff check tests
	ruff check main.py

.PHONY: clean
clean:
	rm -rf build/gen
