{ config, lib, pkgs, inputs, ... }:

{
  networking.networkmanager.enable = true;  # Easiest to use and most distros use this by default.
  services.automatic-timezoned.enable = true;

  boot.loader.systemd-boot.enable = true;

  fileSystems."/" = {
    device = "/dev/disk/by-uuid/00000000-0000-0000-0000-000000000000";
    fsType = "btrfs";
  };

  # Use flakes
  nix.settings = {
    experimental-features = [ "nix-command" "flakes" ];
  };

  users.users.runner = {
    isSystemUser = true;
    home = "/home/runner";
    group = "wheel";
  };

  systemd.services.arbbot = {
    enable = true;

    serviceConfig = {
      ExecStart = "nix develop git+https://github.com/timewave-computer/auction-arbitrage-bot?ref=feature-localinterchaintest --command python main.py --base_denom untrn";
    };
  };

  # List packages installed in system profile. To search, run:
  # $ nix search wget
  environment.systemPackages = with pkgs; [
    wget
    emacs
    git
    feh
    killall
    python3
    gnumake
    black
    nixfmt-classic
    go_1_21
    libgcc
    gcc
    lsof
    cargo
    rustc
    rust-analyzer
    rustfmt
    docker-compose
    zip
    unzip
  ];

  # Enable the OpenSSH daemon.
  services.openssh.enable = true;

  virtualisation.docker.enable = true;
  virtualisation.docker.rootless = {
    enable = true;
    setSocketVariable = true;
  };

  system.stateVersion = "23.11";
}
