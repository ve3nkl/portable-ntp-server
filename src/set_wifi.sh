#!/bin/bash

# Make sure that we are called as root.

if ! [ $(id -u) = 0 ]; then
   echo "The script need to be run as root." >&2
   exit 1
fi

# Check the requested WiFi mode (AP, CLIENT or OFF)

if [[ ( $1 == "AP" ) ]]; then

  systemctl stop dhcpcd-client.service >>/home/pi/set_wifi-log.txt 2>&1
  ip -4 address flush scope global >>/home/pi/set_wifi-log.txt 2>&1
  systemctl start hostapd.service >>/home/pi/set_wifi-log.txt 2>&1
  systemctl start dnsmasq.service >>/home/pi/set_wifi-log.txt 2>&1
  systemctl start dhcpcd.service >>/home/pi/set_wifi-log.txt 2>&1
  
else

  if [[ ( $1 == "CLIENT" ) ]]; then
    systemctl stop hostapd.service >>/home/pi/set_wifi-log.txt 2>&1
    systemctl stop dnsmasq.service >>/home/pi/set_wifi-log.txt 2>&1
    systemctl stop dhcpcd.service >>/home/pi/set_wifi-log.txt 2>&1
    ip -4 address flush scope global >>/home/pi/set_wifi-log.txt 2>&1
    ps -ef | grep dhcpcd >>/home/pi/set_wifi-log.txt 2>&1
    systemctl start dhcpcd-client.service >>/home/pi/set_wifi-log.txt 2>&1
    ifconfig wlan0 >>/home/pi/set_wifi-log.txt 2>&1
    wpa_cli status >>/home/pi/set_wifi-log.txt 2>&1
    wpa_cli list_networks >>/home/pi/set_wifi-log.txt 2>&1
    
  else
  
    systemctl stop hostapd.service >>/home/pi/set_wifi-log.txt 2>&1
    systemctl stop dnsmasq.service >>/home/pi/set_wifi-log.txt 2>&1
    systemctl stop dhcpcd.service >>/home/pi/set_wifi-log.txt 2>&1
    systemctl stop dhcpcd-client.service >>/home/pi/set_wifi-log.txt 2>&1
    ip -4 address flush scope global >>/home/pi/set_wifi-log.txt 2>&1
    
  fi
fi
