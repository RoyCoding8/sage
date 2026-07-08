# Alibaba Cloud Security Groups

## What Are Security Groups?

Security groups act as virtual firewalls for ECS instances. They control which ports and IPs can access your instances.

## Key Concepts

- **Inbound rules:** Control incoming traffic (what can reach your server)
- **Outbound rules:** Control outgoing traffic (what your server can reach)
- **Default:** NO inbound rules (all ports blocked)

## Common Security Group Rules

### Web Server (HTTP/HTTPS)
- Port 80/80 (HTTP) from 0.0.0.0/0
- Port 443/443 (HTTPS) from 0.0.0.0/0

### SSH Access
- Port 22/22 (SSH) from your IP or 0.0.0.0/0

### Application Server
- Port 8080/8080 (App) from 0.0.0.0/0

## Mistake Pattern: Forgotten Security Group

**What happens:**
1. Agent creates ECS instance
2. Agent deploys web server on port 80
3. User tries to access → "Connection refused"
4. Agent checks → security group has NO rules for port 80

**The rule:**
ALWAYS configure security group rules BEFORE deploying the application.

## Step-by-Step (Correct Order)

1. Create security group
2. Add port 80/443/22 rules
3. Create ECS instance with that security group
4. Deploy application
5. Verify access

## Step-by-Step (Wrong Order)

1. Create ECS instance (no security group rules)
2. Deploy application
3. Try to access → FAILS
4. Realize security group missing → need to reconfigure
