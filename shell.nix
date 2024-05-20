{ pkgs ? import <nixpkgs> {} }:

let
  packageOverrides = pkgs.callPackage ./python-packages.nix {};
  python = pkgs.python3.override { inherit packageOverrides; };
  pythonWithPackages = python.withPackages (ps: [
    ps.cosmpy
    ps.schedule
  ]);
in
pkgs.mkShell {
  nativeBuildInputs = with pkgs.buildPackages; [
    gnumake
    protobuf
    protoc-gen-go
    protoc-gen-go-grpc
    pythonWithPackages
  ];

  shellHook = ''
    export PYTHONPATH=src:build/gen
  '';
}
