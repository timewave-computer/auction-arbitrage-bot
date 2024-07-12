{ config, lib, pkgs, inputs, ... }:

{
  networking.networkmanager.enable = true;  # Easiest to use and most distros use this by default.
  services.automatic-timezoned.enable = true;

  programs.zsh = {
    enable = true;
    syntaxHighlighting.enable = true;
    ohMyZsh = {
      enable = true;
      plugins = [ "git" ];
    };
  };

  # Use flakes
  nix.settings = {
    experimental-features = [ "nix-command" "flakes" ];
  };

  # List packages installed in system profile. To search, run:
  # $ nix search wget
  environment.systemPackages = with pkgs; [
    wget
    emacs
    git
    feh
    killall
    zsh-syntax-highlighting
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
