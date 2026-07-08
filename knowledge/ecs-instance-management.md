# ECS Instance Management

## Instance Lifecycle

### Create an Instance
```bash
aliyun ecs RunInstances \
  --ImageId ubuntu_22_04_x64_20G_alibase_20230China.vhd \
  --InstanceType ecs.t6-c1m1.large \
  --SecurityGroupId sg-xxxxx \
  --VSwitchId vsw-xxxxx \
  --InstanceName my-app-server
```
Key parameters: ImageId (OS), InstanceType (CPU/RAM), SecurityGroupId, VSwitchId (subnet).

### Start / Stop / Delete
```bash
aliyun ecs StartInstance --InstanceId i-xxxxx
aliyun ecs StopInstance --InstanceId i-xxxxx --ForceStop true
aliyun ecs DeleteInstance --InstanceId i-xxxxx --Force true
```
Stopping is required before deletion for running instances. Use `--ForceStop true` only when graceful shutdown fails.

## Common Instance Types
| Type | vCPU | RAM | Use Case |
|------|------|-----|----------|
| ecs.t6-c1m1.large | 2 | 2 GB | Dev/test |
| ecs.c6.xlarge | 4 | 8 GB | Web apps |
| ecs.g6.2xlarge | 8 | 32 GB | Databases |

## SSH Access
1. Create a key pair: `aliyun ecs CreateKeyPair --KeyPairName sage-key`
2. Attach at creation via `--KeyPairName sage-key`
3. Connect: `ssh -i sage-key.pem root@<EIP>`

Ensure the security group allows port 22 inbound from your IP (not 0.0.0.0/0 in production).

## Common Errors
- **InvalidInstanceType**: The type is not available in the selected zone. Use `DescribeAvailableResource` to check.
- **QuotaExceeded**: Account limit reached. Submit a ticket or release unused instances.
- **IncorrectInstanceStatus**: Instance must be in `Stopped` state for certain operations (resize, delete).

## Best Practices
- Tag instances with `environment`, `team`, and `app` for cost tracking.
- Use launch templates for repeatable configurations.
- Enable auto-release for temporary test instances to avoid forgotten charges.
- Prefer pay-as-you-go for dev; reserved instances for stable production workloads.
