{ self, config, lib, pkgs, inputs, ... }:

{
  imports = [ <nixpkgs/nixos/modules/virtualisation/google-compute-image.nix> ];

  networking.networkmanager.enable =
    true; # Easiest to use and most distros use this by default.
  services.automatic-timezoned.enable = true;

  # Use flakes
  nix.settings = { experimental-features = [ "nix-command" "flakes" ]; };

  environment.systemPackages = [ pkgs.git self ];

  virtualisation.docker.enable = true;

  systemd.services.setup-env = let
    setup = pkgs.writeShellScript "setup" ''
      nix build flake.nix#protobuf-client-code
      cp -r ${self} /root/env
    '';
  in {
    enable = true;
    wantedBy = [ "multi-user.target" ];
    serviceConfig = { ExecStart = setup; };
  };

  systemd.services.arbbot = {
    enable = true;
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      After = "setup-env.service";
      Environment = "PYTHONPATH=src:${self}/build/gen";
      ExecStart =
        "/run/current-system/sw/bin/nix develop --command python3 ${self}/main.py --base_denom untrn";
      WorkingDirectory = "/root/env";
    };
  };

  systemd.services.local-ic = {
    enable = true;
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      After = "arbbot.service";
      Environment = "HOME=/root/";
      ExecStart =
        "/run/current-system/sw/bin/nix run flake.nix#local-ic -- start neutron_osmosis_gaia --api-port 42069";
      WorkingDirectory = "/root/env/local-interchaintest";
    };
  };

  systemd.services.local-interchaintest = {
    enable = true;
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      After = "local-ic.service";
      ExecStartPre = "/run/current-system/sw/bin/sleep 60";
      ExecStart =
        "/run/current-system/sw/bin/nix run flake.nix#local-interchaintest";
      WorkingDirectory = "/root/env/local-interchaintest";
    };
  };

  # Enable the OpenSSH daemon.
  services.openssh.enable = true;

  system.stateVersion = "23.11";
}
