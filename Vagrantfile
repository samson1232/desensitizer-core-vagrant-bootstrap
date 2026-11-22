# Vagrant — a disposable box that proves the suite, not a deployment target.
#
#   vagrant up      provisions python3 and runs verify.py (98 offline controls)
#   vagrant ssh     then: cd /vagrant && python3 ui_server.py
#
# The whole project is standard library, so there is nothing else to install and no
# local model to download. That is the point of this variant.

Vagrant.configure("2") do |config|
  config.vm.box = "bento/ubuntu-24.04"
  config.vm.box_check_update = false

  # UI server, loopback only inside the box; mapped so the host can open it.
  config.vm.network "forwarded_port", guest: 8799, host: 8799, auto_correct: true

  config.vm.provider "virtualbox" do |vb|
    vb.memory = 1024
    vb.cpus = 2
  end

  config.vm.provision "shell", inline: <<~SHELL
    set -euo pipefail
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq python3
    cd /vagrant
    python3 --version
    python3 verify.py
  SHELL
end
