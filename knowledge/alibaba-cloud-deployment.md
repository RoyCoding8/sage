# Alibaba Cloud ECS Deployment Guide

## ECS Instance Basics

- **Instance Types:** t6 (burstable), s6 (standard), c7 (compute-optimized)
- **Regions:** us-east-1 (Virginia), us-west-1 (Silicon Valley), eu-central-1 (Frankfurt)
- **Images:** Ubuntu 22.04, CentOS 7, Alibaba Cloud Linux 3

## Security Groups (CRITICAL)

Security groups are virtual firewalls that control inbound/outbound traffic.

**Rules:**
1. You MUST create a security group before creating an ECS instance
2. You MUST add ingress rules for ports you want to use (80, 443, 22, etc.)
3. Default security group allows NO inbound traffic
4. Rules are applied immediately

**Common mistakes:**
- Forgetting to add port 80/443 rule → web server unreachable
- Forgetting port 22 rule → SSH access impossible
- Using wrong CIDR block → restricted access

## Deployment Steps

1. Create security group
2. Add ingress rules (port 80, 443, 22)
3. Create ECS instance with security group
4. Connect via SSH
5. Install runtime (Node.js, Python, Docker)
6. Deploy application
7. Verify with curl

## Docker Deployment

```bash
# Install Docker
curl -fsSL https://get.docker.com | sh
systemctl enable docker && systemctl start docker

# Pull and run
docker pull nginx:latest
docker run -d -p 80:80 --name web nginx
```

## Python Deployment

```bash
# Install Python
apt update && apt install -y python3-pip

# Deploy Flask app
pip3 install flask
python3 app.py
```

## Node.js Deployment

```bash
# Install Node.js
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt install -y nodejs

# Deploy
npm install
npm start
```

## Monitoring

- **CPU Usage:** Check via CloudMonitor API
- **Memory Usage:** Check via CloudMonitor API
- **Disk Usage:** Check via CloudMonitor API
- **Network:** Check security group rules if connectivity issues

## Common Errors

| Error | Cause | Fix |
|---|---|---|
| Connection refused | Port not open in security group | Add ingress rule |
| Permission denied | SSH key not configured | Reset password or add key |
| Instance not found | Wrong region or instance ID | Check region and ID |
| Deployment failed | App not installed on instance | Install runtime first |

- node apps require: R001, R002, R003, R004, R005 (learned from Deploy Node.js web app to Alibaba Cloud ECS)

- python apps require: R001, R002, R003, R004, R005 (learned from Deploy Python Flask API to Alibaba Cloud ECS)

- node apps require: R001, R002, R003, R004, R005 (learned from Deploy Node.js Express API to Alibaba Cloud ECS)

- node apps require: R001, R002, R003, R004, R005 (learned from Deploy Node.js web app to Alibaba Cloud ECS)

- python apps require: R001, R002, R003, R004, R005 (learned from Deploy Python Flask API to Alibaba Cloud ECS)

- node apps require: R001, R002, R003, R004, R005 (learned from Deploy Node.js Express API to Alibaba Cloud ECS)

- node apps require: R001, R002, R003, R004, R005 (learned from Deploy Node.js web app to Alibaba Cloud ECS)

- python apps require: R001, R002, R003, R004, R005 (learned from Deploy Python Flask API to Alibaba Cloud ECS)

- node apps require: R001, R002, R003, R004, R005 (learned from Deploy Node.js Express API to Alibaba Cloud ECS)

- node apps require: R001, R002, R003, R004, R005 (learned from Deploy Node.js web app to Alibaba Cloud ECS)

- python apps require: R001, R002, R003, R004, R005 (learned from Deploy Python Flask API to Alibaba Cloud ECS)
