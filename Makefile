PROTO_DIR = 'proto'
PROTO_SOURCES := $(shell find $(PROTO_DIR) -name '*.proto')

proto: build/gen/osmosis $(PROTO_SOURCES)
	protoc --proto_path=$(PROTO_DIR) --python_out=build/gen/osmosis $(PROTO_SOURCES)

build/gen/osmosis:
	mkdir -p $@
