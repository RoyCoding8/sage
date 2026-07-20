# Deployment Patterns

## Blue-Green Deployment
Maintain two identical environments (blue = live, green = staging). Deploy to green, test, then switch traffic.

```bash
# Deploy new version to green
aliyun ecs RunInstances --InstanceName green-v2 --ImageId <new-image> ...

# Health check passes → switch SLB backend
aliyun slb RemoveBackendServers --LoadBalancerId lb-xxxxx \
  --BackendServers '[{"ServerId":"i-blue-old","Weight":"100"}]'
aliyun slb AddBackendServers --LoadBalancerId lb-xxxxx \
  --BackendServers '[{"ServerId":"i-green-new","Weight":"100"}]'
```
Rollback: switch SLB back to the blue instances. Keep blue alive for at least one release cycle.

## Rolling Updates
Update instances one at a time (or in batches) behind a load balancer. No extra infrastructure needed.

```bash
for INSTANCE_ID in i-001 i-002 i-003; do
  aliyun slb RemoveBackendServers --LoadBalancerId lb-xxxxx \
    --BackendServers "[{\"ServerId\":\"${INSTANCE_ID}\"}]"
  ssh root@${INSTANCE_ID} "cd /app && git pull && systemctl restart app"
  sleep 10  # health check interval
  aliyun slb AddBackendServers --LoadBalancerId lb-xxxxx \
    --BackendServers "[{\"ServerId\":\"${INSTANCE_ID}\",\"Weight\":\"100\"}]"
done
```
Risk: if the new version is broken, only one instance is affected before you notice.

## Docker Deployment
```bash
# On the ECS instance
docker pull registry.us-east-1.aliyuncs.com/myns/myapp:v2.1
docker stop myapp && docker rm myapp
docker run -d --name myapp -p 80:3000 --restart unless-stopped \
  registry.us-east-1.aliyuncs.com/myns/myapp:v2.1
```
Use Container Registry (ACR) for private images. Tag images with git SHA for traceability.

## Systemd Services
For non-Docker deployments, manage the app as a systemd service:
```ini
# /etc/systemd/system/myapp.service
[Unit]
Description=My Application
After=network.target

[Service]
Type=simple
User=app
WorkingDirectory=/opt/myapp
ExecStart=/opt/myapp/bin/start.sh
Restart=on-failure
RestartSec=5
Environment=NODE_ENV=production

[Install]
WantedBy=multi-user.target
```
```bash
systemctl daemon-reload && systemctl enable myapp && systemctl start myapp
```

## Common Errors
- **Port already in use**: Previous container/process didn't stop. Use `lsof -i :80` to find and kill it.
- **Image pull auth failure**: Run `docker login` to ACR first or configure credential helpers.
- **Service won't restart**: Check `journalctl -u myapp -n 50` for crash logs. Fix StartLimitBurst if hit.

## Best Practices
- Always health-check before routing traffic to a new instance.
- Use immutable deployments (new image/instance per release) over in-place updates when possible.
- Keep at least N-1 instances healthy during rolling updates (set SLB minimum healthy percentage).
- Automate rollback: if health checks fail within 2 minutes post-deploy, revert automatically.
