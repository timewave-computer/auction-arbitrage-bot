{
  description =
    "A flake providing a reproducible environment for testing the arbitrage bot";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs?ref=24.05";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system: rec {
      local-ic = let
        repo = nixpkgs.fetchFromGitHub {
          owner = "strangelove-ventures";
          repo = "interchaintest";
          rev = "v8.5.0";
          hash = "sha256-NKp0CFPA593UNG/GzMQh7W/poz1/dESrqlRG8VQVxUk=";
        };
      in nixpkgs.buildGoModule rec {
        pname = "local-ic";
        version = "8.5.0";
        src = repo;
        proxyVendor = true;
        subPackages = [ "local-interchain/cmd/local-ic" ];
        vendorHash = "sha256-NWq2/gLMYZ7T5Q8niqFRJRrfnkb0CjipwPQa4g3nCac=";
      };
      local-interchaintest = nixpkgs.rustPlatform.buildRustPackage {
        name = "local-interchaintest";
        src = ./local-interchaintest;
        nativeBuildInputs = nativeBuildInputs;
        buildInputs = buildInputs ++ [ local-ic ];
        cargoSha256 = nixpkgs.lib.fakeHash;
        cargoLock = {
          lockFile = ./local-interchaintest/Cargo.lock;
          outputHashes = {
            "localic-std-0.0.1" =
              "sha256-v2+BGy7aH63B5jR8/oR0CSHOUBgNdfk+8JgNKfOFaq0=";
            "localic-utils-0.1.0" =
              "sha256-1Xg2XSJXqWfCJ4MB6ElrsVYpztXSzAl7HFAZ12QRhfo=";
          };
        };
      };
    });
}
