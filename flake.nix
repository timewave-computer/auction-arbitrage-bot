{
  description = "A flake providing a reproducible environment for arbitrage";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs?ref=24.05";
    flake-utils.url = "github:numtide/flake-utils";
    rust-overlay = {
      url = "github:oxalica/rust-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    deploy-rs.url = "github:serokell/deploy-rs";
  };

  outputs = { self, nixpkgs, flake-utils, rust-overlay, deploy-rs }:
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
        nativeBuildInputs = with pkgs.buildPackages; [
          gnumake
          protobuf
          protoc-gen-go
          protoc-gen-go-grpc
          mypy-protobuf
          black
          grpc
          grpc_cli
          ruff
          rust-bin.stable.latest.default
          pkg-config
        ];
        buildInputs = with pkgs; [ openssl ];
      in rec {
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

        packages.local-interchaintest = pkgs.stdenv.mkDerivation {
          src = ./local-interchaintest;
          name = "local-interchaintest";
          nativeBuildInputs = nativeBuildInputs;
          buildInputs = buildInputs;
          buildPhase = ''
            cargo build --release
          '';
          installPhase = ''
            mkdir -p $out/bin
            cp target/release/local-interchaintest $out/bin/local-interchaintest
            chmod +x $out
          '';
        };

        devShells.default = pkgs.mkShell {
          nativeBuildInputs = nativeBuildInputs;
          buildInputs = buildInputs;
          packages = [ pythonWithPackages packages.local-ic ];
          shellHook = ''
            export PYTHONPATH=src:build/gen
          '';
        };
      }) // {
        deploy.nodes.testrunner = {
          hostname = "34.121.54.8";
          profiles = {
            system = {
              user = "root";
              path = deploy-rs.lib.x86_64-linux.activate.nixos
                self.nixosConfigurations.testrunner;
            };
          };
        };

        nixosConfigurations.testrunner = nixpkgs.lib.nixosSystem {
          system = "x86_64-linux";
          modules = [ ./test_runner_conf.nix ];
          defaultPackage = import ./test_runner.nix nixpkgs;
        };

        checks = builtins.mapAttrs
          (system: deployLib: deployLib.deployChecks self.deploy) deploy-rs.lib;
      };
}
