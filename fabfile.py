
from fabric import Connection
from invoke import Responder
from fabric import task
from patchwork.files import append
import json
import uuid

import os
import logging

logging.raiseExceptions=False

BASE_DIR = os.path.dirname(os.path.dirname(__file__))

from config import (NEW_PASSWORD,
                    NEW_USERNAME,
                    NEW_HOSTNAME,
                    ORIGINAL_HOSTNAME,
                    ORIGINAL_PASSWORD,
                    ORIGINAL_USERNAME,
                    ACCESS_IP,
                    CERTS_NAME,
                    TUNNEL_CERTS_NAME
                    )

origional_host = ACCESS_IP if ACCESS_IP is not None else "%s.local" % ORIGINAL_HOSTNAME
new_host = ACCESS_IP if ACCESS_IP is not None else "%s.local" % NEW_HOSTNAME

# default_hosts = ["%s:%s" % (default_host, 22)]
# renamed_hosts = ["%s.local:%s" % (NEW_HOSTNAME, 22)]

CERTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "secrets", "keys"))
DRIVERS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "drivers"))
USB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "usb"))

if not os.path.exists(CERTS_DIR):
    raise Exception("couldn't find certs")


def get_cert_path(private=False, certs_name=CERTS_NAME):
    if private:
        return os.path.join(CERTS_DIR, certs_name)
    else:
        return os.path.join(CERTS_DIR, "%s.pub" % certs_name)


cert_path = get_cert_path(private=True)

cert_cxn = Connection(host=origional_host,
                      user=NEW_USERNAME,
                      connect_kwargs={
                          "key_filename": cert_path,
                      },
                      port=22)

RASPBIAN_VERSION = "2019-04-08-raspbian-stretch-lite"

@task
def settings(junk, number):

    public_key_file = get_cert_path(private=False, certs_name=TUNNEL_CERTS_NAME)
    private_key_file = get_cert_path(private=True, certs_name=TUNNEL_CERTS_NAME)

    with open(public_key_file, "r") as f:
        public_key = f.read()

    with open(private_key_file, "r") as f:
        private_key = f.read()

    # private_key = private.exportKey('PEM').decode("utf-8")
    # public_key = public.exportKey('OpenSSH').decode("utf-8")

    device_uuid = str(uuid.uuid4())

    name = "DSK-%s" % number

    settings = {
        "name": name,
        "description": "An ishiki desk",
        "url": "https://eightfitzroy.arupiot.com/ishiki/%s" % name,
        "public_key": public_key,
        "private_key": private_key,
        "uuid": device_uuid,
        "host_name": "ishiki-%s" % name,
        "tunnel_host": "35.205.94.204",
        "docker_tunnel_port": "%s" % (5000 + int(number)),
        "admin_tunnel_port": "%s" % (7000 + int(number)),
        "tunnel_user": "ishiki_tunnel",
        "time_zone": "Europe/London",
        "ssid": "xxxxxx",
        "psk": "xxxxxx",
        "eth0_address": "",
        "eth0_netmask": "",
        "eth0_gateway": "",
        "wlan0_address": "",
        "wlan0_netmask": "",
        "wlan0_gateway": ""
    }

    usb_dir = os.path.join(USB_DIR, name)

    if not os.path.exists(usb_dir):
        os.makedirs(usb_dir)

    path = os.path.join(usb_dir, "settings.json")

    with open(path, "w") as f:
        f.write(json.dumps(settings, sort_keys=True, indent=4))


@task
def prepare(junk, screen=None):
    """
    Prepares the base image
    """

    pi_cxn = Connection(host=origional_host,
                     user=ORIGINAL_USERNAME,
                     connect_kwargs={"password": ORIGINAL_PASSWORD},
                     port=22)

    create_new_user(pi_cxn)

    new_user_cxn = Connection(host=origional_host,
                     user=NEW_USERNAME,
                     connect_kwargs={"password": NEW_PASSWORD},
                     port=22)

    copy_certs(new_user_cxn)

    install_pip(cert_cxn)
    install_extra_libs(cert_cxn)
    install_docker(cert_cxn)
    remove_bloat(cert_cxn)
    configure_rsyslog(cert_cxn)
    daily_reboot(cert_cxn)
    _add_config_file(cert_cxn, "wpa_supplicant.backup", "/etc/wpa_supplicant/wpa_supplicant.backup", "root", chmod="644")

    # installing screen drivers as pi for waveshare quirks
    if screen:
        install_screen_drivers(pi_cxn, screen)
    cert_cxn.sudo('reboot now')


@task
def finish(junk, screen=None, mode="prod"):

    update_boot_config(cert_cxn, screen)

    if mode == "prod":
        reduce_writes(cert_cxn)
    else:
        install_samba(cert_cxn)

    set_ssh_config(cert_cxn, mode)

    delete_old_user(cert_cxn)
    add_bootstrap(cert_cxn)
    cert_cxn.sudo("sudo python3 /opt/ishiki/bootstrap/clean_wifi.py")
    set_hostname(cert_cxn)
    cert_cxn.sudo('shutdown now')


######################################################################

def update_boot_config(cxn, screen_name):

    if screen_name == "waveshare":
        config_filename = "waveshare_config.txt"
        _add_config_file(cxn, config_filename, "/boot/config.txt", "root")
    elif screen_name == "kedei":
        install_kedei_drivers(cxn)
        config_filename = "kedei_config.txt"
        _add_config_file(cxn, config_filename, "/boot/config.txt", "root")
    else:
        config_filename = "config.txt"


def install_screen_drivers(cxn, screen_name):

    if screen_name == "waveshare":
        install_waveshare_drivers(cxn)
        config_filename = "waveshare_config.txt"
        _add_config_file(cxn, config_filename, "/boot/config.txt", "root")
    elif screen_name == "kedei":
        install_kedei_drivers(cxn)
        config_filename = "kedei_config.txt"
    else:
        config_filename = "config.txt"


def delete_old_user(cxn):
    cxn.sudo("deluser %s" % ORIGINAL_USERNAME)


def create_new_user(cxn):

    sudopass = Responder(pattern=r'UNIX password:',
                         response='%s\n' % NEW_PASSWORD)

    accept = Responder(pattern=r'\[\]:',
                         response='\n')

    yes = Responder(pattern=r'\[Y/n\]',
                         response='\n')

    cxn.sudo("adduser %s" % NEW_USERNAME, pty=True, watchers=[sudopass, accept, yes])

    # make sudo
    cxn.sudo("usermod -aG sudo %s" % NEW_USERNAME)

    # sudo without password
    append_text(cxn, "/etc/sudoers.d/%s-nopasswd" % NEW_USERNAME, "%s ALL=(ALL) NOPASSWD:ALL" % NEW_USERNAME)

    cxn.sudo("sudo chmod 644 /etc/sudoers.d/%s-nopasswd" % NEW_USERNAME)


def append_text(cxn, file_path, text):
    cxn.sudo('echo "%s" | sudo tee -a %s' % (text, file_path))


def command_in_dir(cxn, command, dir):
    cxn.sudo('sh -c "cd %s; %s"' % (dir, command))



def configure_rsyslog(cxn):
    _add_config_file(cxn, "rsyslog.conf", "/etc/rsyslog.conf", "root", chmod="644")


def daily_reboot(cxn):
    append_text(cxn, "/etc/crontab", "0 4    * * *   root    /sbin/shutdown -r +5")


def copy_certs(cxn):
    cxn.run("mkdir /home/%s/.ssh" % NEW_USERNAME)
    cxn.run("chmod 700 /home/%s/.ssh" % NEW_USERNAME)
    cert_path = get_cert_path()
    cxn.put(cert_path, "/home/%s/.ssh/authorized_keys" % NEW_USERNAME)
    cxn.run("chmod 600 /home/%s/.ssh/authorized_keys" % NEW_USERNAME)


def set_ssh_config(cxn, mode):
    if mode == "dev":
        _add_config_file(cxn, "sshd_config_dev", "/etc/ssh/sshd_config", "root", chmod="600")
    else:
        _add_config_file(cxn, "sshd_config", "/etc/ssh/sshd_config", "root", chmod="600")
    cxn.sudo("systemctl restart ssh")


def install_pip(cxn):
    cxn.sudo("apt-get update")
    cxn.sudo("apt-get install -y curl python3-distutils python3-testresources")
    cxn.sudo("curl https://bootstrap.pypa.io/get-pip.py | sudo python3")
    #cxn.sudo("curl --silent --show-error --retry 5 https://bootstrap.pypa.io/" "get-pip.py | sudo python3")

def install_samba(cxn):
    cxn.sudo("apt-get -y install samba")
    _add_config_file(cxn, "smb.conf", "/etc/samba/smb.conf", "root")
    cxn.sudo("/etc/init.d/samba-ad-dc restart")

    smbpass = Responder(pattern=r'SMB password:',
                         response='%s\n' % NEW_PASSWORD)

    cxn.sudo("smbpasswd -a %s" % NEW_USERNAME, pty=True, watchers=[smbpass])


def install_extra_libs(cxn):
    cxn.sudo("apt-get update")
    cxn.sudo("pip install --user wheel")
    cxn.sudo("pip install --upgrade pip")
    cxn.sudo("apt-get -y install libssl-dev python-nacl python3-dev python3-distutils python3-testresources python-cryptography git cmake ntp autossh libxi6 libffi-dev")
    cxn.sudo("pip install pyudev")
    cxn.sudo("pip install pyroute2")


def install_docker(cxn):

    # install docker
    cxn.sudo("curl -sSL get.docker.com | sh")

    # fix the docker host in json problem
    _add_config_file(cxn, "docker.service", "/lib/systemd/system/docker.service", "root", chmod=755)

    # config deamon
    _add_config_file(cxn, "daemon.json", "/etc/docker/daemon.json", "root")

    # sets up service
    cxn.sudo("systemctl enable docker")
    # sudo("groupadd docker")
    # allows users to use to use docker
    cxn.sudo("usermod -aG docker %s" % NEW_USERNAME)
    # installs docker compose
    cxn.sudo("pip install docker-compose")


def remove_bloat(cxn):
    cxn.sudo('apt update')
    cxn.sudo("apt-get -y remove --purge libreoffice*")
    cxn.sudo("apt-get -y remove --purge wolfram*")
    cxn.sudo("apt-get -y remove modemmanager")
    cxn.sudo("apt-get -y remove --purge minecraft*")
    cxn.sudo("apt-get -y purge --auto-remove scratch")
    cxn.sudo("dpkg --remove flashplugin-installer")
    cxn.sudo("apt-get clean")
    cxn.sudo("apt-get autoremove")


def set_hostname(cxn):
    cxn.sudo("sed -i 's/%s/%s/g' /etc/hostname" % (ORIGINAL_HOSTNAME, NEW_HOSTNAME))
    cxn.sudo("sed -i 's/%s/%s/g' /etc/hosts" % (ORIGINAL_HOSTNAME, NEW_HOSTNAME))
    cxn.sudo("hostname %s" % NEW_HOSTNAME)



def _add_config_file(cxn, name, dst, owner, chmod=None):

    cxn.put("config_files/%s" % name, "put_temp")
    cxn.sudo("cp put_temp %s" % dst)
    cxn.sudo("rm put_temp")
    if chmod is not None:
        cxn.sudo("chmod %s %s" % (chmod, dst))
    cxn.sudo("chown %s %s" % (owner, dst))
    cxn.sudo("chgrp %s %s" % (owner, dst))


def _add_software_file(cxn, name, dst, owner, chmod=755):

    cxn.put("bootstrap/%s" % name, "put_temp")
    cxn.sudo("mv put_temp %s" % dst)
    cxn.sudo("chmod %s %s" % (chmod, dst))
    cxn.sudo("chown %s %s" % (owner, dst))
    cxn.sudo("chgrp %s %s" % (owner, dst))


def _put_file(cxn, src, dst, owner, chmod=None):
    cxn.put(src, "put_temp")
    cxn.sudo("mv put_temp %s" % dst)
    if chmod is not None:
        cxn.sudo("chmod %s %s" % (chmod, dst))
    cxn.sudo("chown %s %s" % (owner, dst))
    cxn.sudo("chgrp %s %s" % (owner, dst))


def reboot(cxn):
    print('System reboot')
    cxn.sudo('reboot now')
#
# def shutdown():
#     print('shutdown')
#     sudo('shutdown now')
#
# def halt():
#     print('System halt')
#     sudo('halt')
#




def reduce_writes(cxn):

    # a set of optimisations from
    # http://www.zdnet.com/article/raspberry-pi-extending-the-life-of-the-sd-card/
    # and
    # https://narcisocerezo.wordpress.com/2014/06/25/create-a-robust-raspberry-pi-setup-for-24x7-operation/

    # minimise writes
    use_ram_partitions(cxn)
    _stop_fsck_running(cxn)
    _remove_swap(cxn)

    # _redirect_logrotate_state()
    # _dont_update_fake_hwclock()
    # _dont_do_man_indexing()

def use_ram_partitions(cxn):

    append_text(cxn, "/etc/fstab", "tmpfs    /tmp    tmpfs    defaults,noatime,nosuid,size=100m    0 0")
    append_text(cxn, "/etc/fstab", "tmpfs    /var/tmp    tmpfs    defaults,noatime,nosuid,size=30m    0 0")
    append_text(cxn, "/etc/fstab", "tmpfs    /var/log    tmpfs    defaults,noatime,nosuid,mode=0755,size=100m    0 0")

#
# def _redirect_logrotate_state():
#     sudo("rm /etc/cron.daily/logrotate")
#     _add_config_file("logrotate", "/etc/cron.daily/logrotate", "root", chmod="755")
#

def _stop_fsck_running(cxn):
    cxn.sudo("tune2fs -c -1 -i 0 /dev/mmcblk0p2")
#
# def _dont_update_fake_hwclock():
#     sudo("rm /etc/cron.hourly/fake-hwclock")
#
# def _dont_do_man_indexing():
#     sudo("rm  /etc/cron.weekly/man-db")
#     sudo("rm  /etc/cron.daily/man-db")


def _remove_swap(cxn):
    cxn.sudo("update-rc.d -f dphys-swapfile remove")
    cxn.sudo("swapoff /var/swap")
    cxn.sudo("rm /var/swap")


def add_bootstrap(cxn):

    cxn.sudo("mkdir -p /opt/ishiki/bootstrap")

    file_names = ["start.sh",
                  "bootstrap.py",
                  "mount.py",
                  "monitor.py",
                  "clean_wifi.py",
                  "resize_once.txt",
                  "tunnel.service.template"
                  ]

    for name in file_names:
        _add_software_file(cxn, name, "/opt/ishiki/bootstrap/%s" % name, "root")

    _add_config_file(cxn, "ishiki-bootstrap.service", "/etc/systemd/system/ishiki-bootstrap.service", "root", chmod=755)

    # sets up service
    cxn.sudo("systemctl enable ishiki-bootstrap")


#################################################################

def install_waveshare_drivers(cxn):
    waveshare_download_touchscreen_driver(cxn)
    waveshare_install_touchscreen_driver(cxn)


def waveshare_download_touchscreen_driver(cxn):
    cxn.sudo('mkdir -p /opt/waveshare')
    # command_in_dir(cxn, "git clone https://github.com/waveshare/LCD-show.git", "/opt/waveshare")
    cxn.sudo("git clone https://github.com/waveshare/LCD-show.git")


def waveshare_install_touchscreen_driver(cxn):

    # Enable I2C
    # See https://learn.adafruit.com/adafruits-raspberry-pi-lesson-4-gpio-setup/configuring-i2c#installing-kernel-support-manually
    cxn.sudo("mkdir -p /boot/overlays")
    cxn.sudo('echo "dtparam=i2c1=on" | sudo tee -a /boot/config.txt')
    cxn.sudo('echo "dtparam=i2c_arm=on" | sudo tee -a /boot/config.txt')
    cxn.sudo('echo "i2c-bcm2708" | sudo tee -a /etc/modules')
    cxn.sudo('echo "i2c-dev" | sudo tee -a /etc/modules')

    # Disable Serial Console
    cxn.sudo('sudo sed -i \'s/console=serial0,115200//\' /boot/cmdline.txt')
    cxn.sudo('sudo sed -i \'s/console=ttyAMA0,115200//\' /boot/cmdline.txt')
    cxn.sudo('sudo sed -i \'s/kgdboc=ttyAMA0,115200//\' /boot/cmdline.txt')

    command_in_dir(cxn, "./LCD35B-show-V2", "/home/pi/LCD-show")
    # cxn.sudo("LCD-show/LCD35B-show-V2")

    print('Installing new kernel for Waveshare touchscreen driver completed')


def install_kedei_drivers(cxn):
    cxn.sudo("mkdir -p /opt/kedei")

    tar_file_path = os.path.join(DRIVERS_DIR, "LCD_show_v6_1_3.tar.gz")
    if os.path.exists(tar_file_path):
        # copy from local dir
        _put_file(cxn, tar_file_path, "/opt/kedei/LCD_show_v6_1_3.tar.gz", "root")
    else:
        # download
        cxn.sudo('sh -c "cd /opt/kedei; wget http://www.kedei.net/raspberry/v6_1/LCD_show_v6_1_3.tar.gz"')

    # untar
    cxn.sudo('sh -c "cd /opt/kedei; tar zxvf /opt/kedei/LCD_show_v6_1_3.tar.gz"')
    cxn.sudo("rm /opt/kedei/LCD_show_v6_1_3.tar.gz")
    kedei_install_new_kernel(cxn)


def kedei_install_new_kernel(cxn):

    print('Installing new kernel for Kedei touchscreen driver')

    # Enable I2C
    # See https://learn.adafruit.com/adafruits-raspberry-pi-lesson-4-gpio-setup/configuring-i2c#installing-kernel-support-manually
    cxn.sudo('echo "dtparam=i2c1=on" | sudo tee -a /boot/config.txt')
    cxn.sudo('echo "dtparam=i2c_arm=on" | sudo tee -a /boot/config.txt')
    cxn.sudo('echo "i2c-bcm2708" | sudo tee -a /etc/modules')
    cxn.sudo('echo "i2c-dev" | sudo tee -a /etc/modules')

    # Disable Serial Console
    cxn.sudo('sudo sed -i \'s/console=serial0,115200//\' /boot/cmdline.txt')
    cxn.sudo('sudo sed -i \'s/console=ttyAMA0,115200//\' /boot/cmdline.txt')
    cxn.sudo('sudo sed -i \'s/kgdboc=ttyAMA0,115200//\' /boot/cmdline.txt')

    cxn.sudo('sh -c "cd /opt/kedei/LCD_show_v6_1_3; cp -v ./lcd_35_v/kernel.img /boot/kernel.img"')
    cxn.sudo('sh -c "cd /opt/kedei/LCD_show_v6_1_3; cp -v ./lcd_35_v/kernel7.img /boot/"')
    cxn.sudo('sh -c "cd /opt/kedei/LCD_show_v6_1_3; cp -v ./lcd_35_v/*.dtb /boot/"')
    cxn.sudo('sh -c "cd /opt/kedei/LCD_show_v6_1_3; cp -v ./lcd_35_v/overlays/*.dtb* /boot/overlays/"')
    cxn.sudo('sh -c "cd /opt/kedei/LCD_show_v6_1_3; cp -v -rf ./lcd_35_v/lib/* /lib/"')

    cxn.sudo('apt-mark hold raspberrypi-kernel')
    print('Installing new kernel for Kedei touchscreen driver completed')
