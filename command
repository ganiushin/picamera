git clone https://github.com/mpromonet/v4l2rtspserver.git


sudo firewall-cmd --permanent --remove-service=dhcpv6-client

rich rules: 
      rule family="ipv4" source address="192.168.1.56" port port="8554" protocol="tcp" accept
      rule family="ipv4" source address="192.168.1.56" port port="8554" protocol="udp" accept
