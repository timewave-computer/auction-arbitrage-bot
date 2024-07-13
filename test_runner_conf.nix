{ self, config, lib, pkgs, inputs, ... }:

{
  imports = [ <nixpkgs/nixos/modules/virtualisation/google-compute-image.nix> ];

  networking.networkmanager.enable =
    true; # Easiest to use and most distros use this by default.
  services.automatic-timezoned.enable = true;

  # Use flakes
  nix.settings = { experimental-features = [ "nix-command" "flakes" ]; };

  environment.systemPackages = [ pkgs.git self ];

  systemd.services.arbbot = {
    enable = true;
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      After = "repo-update.service";
      ExecStart =
        "/run/current-system/sw/bin/nix develop --command main.py --base_denom untrn";
      WorkingDirectory = self;
    };
  };

  systemd.services.local-ic = {
    enable = true;
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      After = "repo-update.service";
      ExecStart =
        "/run/current-system/sw/bin/nix run flake.nix#local-ic -- neutron_osmosis_gaia --api-port 42069";
      WorkingDirectory = "${self}/local-interchaintest";
    };
  };

  systemd.services.local-interchaintest = {
    enable = true;
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      After = "local-ic.service";
      ExecStart =
        "/run/current-system/sw/bin/nix run flake.nix#local-interchaintest";
      WorkingDirectory = "${self}/local-interchaintest";
    };
  };

  # Enable the OpenSSH daemon.
  services.openssh.enable = true;

  system.stateVersion = "23.11";
}
