# Local staging VM for the Jenkins pipeline: Ubuntu 22.04 at 192.168.56.10.
#   vagrant up
#   vagrant ssh-config   (the private key shown here goes into the Jenkins 'staging-ssh' credential)
Vagrant.configure("2") do |config|
  config.vm.box = "ubuntu/jammy64"
  config.vm.hostname = "resilienceops-staging"
  config.vm.network "private_network", ip: "192.168.56.10"

  config.vm.provider "virtualbox" do |vb|
    vb.name = "resilienceops-staging"
    vb.memory = 2048
    vb.cpus = 2
  end
end
