# Monitoring and Alerts

## CloudMonitor Overview
Alibaba Cloud CloudMonitor collects metrics from ECS, RDS, SLB, and other services automatically.
No agent install is needed for basic metrics (CPU, network). Install the CloudMonitor agent for memory and disk metrics.

### Install the Agent
```bash
ARGUS_VERSION=3.5.7
wget "http://cms-download.aliyun-inc.com/agent/cms_go_agent_${ARGUS_VERSION}_linux_amd64.tar.gz"
tar xzf cms_go_agent_*.tar.gz && cd agent && ./install.sh
```

## Key Metrics

| Metric | Namespace | Unit | Alert Threshold |
|--------|-----------|------|-----------------|
| CPUUtilization | acs_ecs_dashboard | % | > 80% for 5 min |
| memory_usedutilization | acs_ecs_dashboard | % | > 85% for 5 min |
| diskusage_utilization | acs_ecs_dashboard | % | > 90% |
| IntranetInRate | acs_ecs_dashboard | bps | context-dependent |

## Creating Alerts
```bash
aliyun cms PutMetricRuleTargets \
  --RuleId rule-xxxxx \
  --Targets '[{"Id":"1","Arn":"acs:mns:us-east-1:1234:/queues/sage-alerts"}]'

aliyun cms PutResourceMetricRule \
  --RuleId sage-cpu-alert \
  --Namespace acs_ecs_dashboard \
  --MetricName CPUUtilization \
  --Resources '[{"instanceId":"i-xxxxx"}]' \
  --Escalations.Critical.Statistics Average \
  --Escalations.Critical.ComparisonOperator GreaterThanThreshold \
  --Escalations.Critical.Threshold 80 \
  --Escalations.Critical.Times 3
```

## Dashboards
Create custom dashboards in the CloudMonitor console or via API:
```bash
aliyun cms CreateDashboard --DashboardName sage-overview \
  --DashboardBody '[{"type":"metric","title":"CPU","metrics":[...]}]'
```

## Common Errors
- **Agent not reporting**: Check that the agent process is running (`ps aux | grep CmsGoAgent`) and port 3128 is not blocked.
- **Missing memory/disk metrics**: The CloudMonitor agent is required. Basic metrics (CPU, network) work without it.
- **Alert not firing**: Verify the evaluation period and threshold. Use `DescribeMetricLast` to check current values.

## Best Practices
- Set alerts on leading indicators (CPU, memory) before they cause outages.
- Use silence windows during planned maintenance to avoid alert storms.
- Group related instances with application groups for unified monitoring.
- Retain metric data exports for capacity planning (CloudMonitor stores 31 days by default).
