PROTO_DIR = 'proto'
PROTO_SOURCES := $(shell find $(PROTO_DIR) -name '*.proto')

test:
	PYTHONPATH=src:build/gen python -m pytest tests

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

proto: build/gen $(PROTO_SOURCES)
	protoc --proto_path=$(PROTO_DIR) --python_out=build/gen --mypy_out=build/gen $(PROTO_SOURCES)

build/gen:
	mkdir -p $@

clean:
	rm -rf build/gen
