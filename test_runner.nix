nixpkgs:
let
  pkgs = nixpkgs.legacyPackages.x86_64-linux;
in pkgs.writeShellScriptBin "run_tests" ''
nix develop --command local-ic run neutron_osmosis_gaia --api-port 42069 &
cargo run
''
