{
  description = "A flake providing a reproducible environment for arbitrage";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs?ref=24.05";
    flake-utils.url = "github:numtide/flake-utils";
    rust-overlay = {
      url = "github:oxalica/rust-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    local-ic = {
      url = "git+file:.?dir=local-interchaintest";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    local-interchaintest = {
      url = "git+file:.?dir=local-interchaintest";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, rust-overlay, local-ic
    , local-interchaintest }@inputs:
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
            pytest-cov
            (skipCheckTests aiohttp)
            (skipCheckTests aiodns)
          ]);
      in rec {
        packages.protobuf-client-code = pkgs.stdenv.mkDerivation {
          name = "protobuf-client-code";
          pname = "protobuf-client-code";
          version = "0.1.0";
          src = ./.;
          buildInputs = with pkgs.buildPackages; [ gnumake protobuf protoc-gen-go protoc-gen-go-grpc mypy-protobuf ];
          buildPhase = ''
            make proto
          '';
          installPhase = ''
            mkdir -p $out/build
            cp -r build $out/build
          '';
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
            export PYTHONPATH=src:build/gen
          '';
        };
      }) // {
        nixosConfigurations."arbbot-test-runner.us-central1-a.c.arb-bot-429100.internal" =
          nixpkgs.lib.nixosSystem {
            system = "x86_64-linux";
            specialArgs = { inherit inputs; inherit self; };
            modules = [ ./test_runner_conf.nix ];
          };
      };
}
