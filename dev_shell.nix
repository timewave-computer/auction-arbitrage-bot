{ pkgs, flake-utils, ... }:

(pkgs.mkShell {
  nativeBuildInputs = nativeBuildInputs;
  buildInputs = buildInputs;
  packages = [ pythonWithPackages local-ic local-interchaintest ];
  shellHook = ''
    export PYTHONPATH=src:build/gen
  '';
})
