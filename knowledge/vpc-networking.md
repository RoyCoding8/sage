# VPC Networking

## VPC and Subnets (VSwitches)

### Create a VPC
```bash
aliyun vpc CreateVpc --VpcName sage-vpc --CidrBlock 172.16.0.0/12
```

### Create a VSwitch (Subnet)
```bash
aliyun vpc CreateVSwitch \
  --VpcId vpc-xxxxx \
  --ZoneId us-east-1a \
  --CidrBlock 172.16.0.0/24 \
  --VSwitchName sage-subnet-public
```
Each VSwitch lives in one availability zone. Use at least two zones for HA.

## Elastic IPs (EIP)

### Allocate and Bind
```bash
aliyun vpc AllocateEipAddress --Bandwidth 5 --InternetChargeType PayByTraffic
aliyun vpc AssociateEipAddress --AllocationId eip-xxxxx --InstanceId i-xxxxx
```
EIPs persist across instance stops/starts. Release when no longer needed to avoid charges.

### Unbind and Release
```bash
aliyun vpc UnassociateEipAddress --AllocationId eip-xxxxx --InstanceId i-xxxxx
aliyun vpc ReleaseEipAddress --AllocationId eip-xxxxx
```

## NAT Gateways
Use a NAT Gateway for private instances that need outbound internet access (pulling packages, updates).
```bash
aliyun vpc CreateNatGateway --VpcId vpc-xxxxx --Name sage-nat --VSwitchId vsw-xxxxx
aliyun vpc CreateSnatEntry --SnatTableId stb-xxxxx --SnatIp 47.x.x.x --SourceCIDR 172.16.0.0/24
```

## Route Tables
Each VPC has a default route table. Custom routes send traffic to NAT, VPN, or peering connections.
```bash
aliyun vpc CreateRouteEntry --RouteTableId rtb-xxxxx \
  --DestinationCidrBlock 0.0.0.0/0 --NextHopId ngw-xxxxx --NextHopType NatGateway
```

## Common Errors
- **InvalidCidrBlock.Overlapped**: VSwitch CIDR overlaps with an existing one. Use non-overlapping /24 blocks.
- **QuotaExceeded.Eip**: Max EIPs reached (default 20). Request an increase or release unused ones.
- **InvalidVSwitchId.NotFound**: The VSwitch was deleted or is in a different region. Double-check region/zone.

## Best Practices
- Use private subnets for databases and backend; public subnets (with EIP/NAT) for web-facing services.
- Never assign public IPs directly to instances that don't need inbound traffic — use NAT for outbound only.
- Peer VPCs instead of routing through the public internet for cross-VPC communication.
- Document your CIDR allocation plan to avoid future conflicts as the project grows.
