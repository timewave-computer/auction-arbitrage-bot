{
  description = "A flake providing a reproducible environment for arbitrage";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs?ref=24.05";
    flake-utils.url = "github:numtide/flake-utils";
    rust-overlay = {
      url = "github:oxalica/rust-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, rust-overlay }@inputs:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          overlays = [ rust-overlay.overlays.default ];
        };
        packageOverrides = pkgs.callPackage ./python-packages.nix { };
        skipCheckTests = drv:
          drv.overridePythonAttrs (old: { doCheck = false; });
        python = pkgs.python312.override { inherit packageOverrides; };
        pythonWithPackages = python.withPackages (ps:
          with ps; [
            cosmpy
            schedule
            python-dotenv
            aiostream
            pytest
            pytest-asyncio
            types-protobuf
            types-pytz
            types-setuptools
            mypy
            (skipCheckTests aiohttp)
            (skipCheckTests aiodns)
          ]);
      in rec {
        packages.protobuf-client-code = pkgs.stdenv.mkDerivation {
          name = "protobuf-client-code";
          pname = "protobuf-client-code";
          version = "0.1.0";
          src = ./.;
          buildInputs = with pkgs.buildPackages; [
            gnumake
            protobuf
            protoc-gen-go
            protoc-gen-go-grpc
            mypy-protobuf
          ];
          buildPhase = ''
            make proto
          '';
          installPhase = ''
            mkdir -p $out
            cp -r build $out
          '';
        };

        packages.local-ic = let
          repo = pkgs.fetchFromGitHub {
            owner = "strangelove-ventures";
            repo = "interchaintest";
            rev = "v8.5.0";
            hash = "sha256-NKp0CFPA593UNG/GzMQh7W/poz1/dESrqlRG8VQVxUk=";
          };
        in pkgs.buildGoModule rec {
          pname = "local-ic";
          version = "8.5.0";
          src = repo;
          proxyVendor = true;
          subPackages = [ "local-interchain/cmd/local-ic" ];
          vendorHash = "sha256-NWq2/gLMYZ7T5Q8niqFRJRrfnkb0CjipwPQa4g3nCac=";
        };

        packages.local-interchaintest = pkgs.rustPlatform.buildRustPackage {
          name = "local-interchaintest";
          src = ./.;
          nativeBuildInputs = [ pkgs.libiconv pkgs.pkg-config ];
          buildInputs = [ pkgs.openssl packages.local-ic ];
          cargoSha256 = "sha256-XAjcq0XKl4UcrfAGLmBdQbmWqNjTIbF3q70vOZSO5gQ=";
          cargoLock = {
            lockFile = ./Cargo.lock;
            outputHashes = {
              "localic-std-0.0.1" =
                "sha256-v2+BGy7aH63B5jR8/oR0CSHOUBgNdfk+8JgNKfOFaq0=";
              "localic-utils-0.1.0" =
                "sha256-1Xg2XSJXqWfCJ4MB6ElrsVYpztXSzAl7HFAZ12QRhfo=";
            };
          };
        };

        devShells.default = pkgs.mkShell {
          nativeBuildInputs = with pkgs.buildPackages; [
            gnumake
            black
            grpc
            grpc_cli
            ruff
            rust-bin.stable.latest.default
            pkg-config
            pkgs.deploy-rs
          ];
          buildInputs = with pkgs; [ openssl packages.protobuf-client-code ];
          packages = [ pythonWithPackages ];
          shellHook = ''
            export PYTHONPATH=src:${packages.protobuf-client-code}/build/gen
          '';
        };
      }) // {
        nixosConfigurations."arbbot-test-runner.us-central1-a.c.arb-bot-429100.internal" =
          nixpkgs.lib.nixosSystem {
            system = "x86_64-linux";
            specialArgs = {
              inherit inputs;
              inherit self;
            };
            modules = [ ./test_runner_conf.nix ];
          };
      };
}
