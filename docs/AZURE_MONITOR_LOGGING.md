# Container Apps logging

the public ERP demo deployment uses the `azure-monitor` environment destination. The diagnostic
setting `container-logs-to-workspace` routes exactly `ContainerAppConsoleLogs`
and `ContainerAppSystemLogs` to the existing Log Analytics workspace. The
workspace remains on PerGB2018 with 30 days of retention. Do not add equivalent
app-level routes, metrics export, or `allLogs` unless additional collection and
its costs are intended.

This replaces the legacy `log-analytics` destination whose HTTP Data Collector
API support ended on 2026-09-14. The application continues writing to stdout and
stderr; no application SDK or custom Data Collection Rule is required for this
platform-managed route. Terraform owns the destination and diagnostic setting;
the deployment root is `terraform/app`.

## Queries and history

New records use `ContainerAppConsoleLogs` and `ContainerAppSystemLogs`, with
standard fields such as `ContainerAppName`, `RevisionName`, `Log`, and `Stream`.
Historical records stay in the corresponding `_CL` tables with typed field
suffixes (for example `ContainerAppName_s` and `Log_s`) until retention expires.
Do not copy or re-ingest historical records.

```kusto
ContainerAppConsoleLogs
| where TimeGenerated > ago(24h)
| project TimeGenerated, ContainerAppName, RevisionName, Stream, Log
| order by TimeGenerated desc
```

For billed console-log volume:

```kusto
ContainerAppConsoleLogs
| where TimeGenerated > ago(24h) and _IsBillable =~ "true"
| summarize Records=count(), BilledGB=sum(_BilledSize)/1e9 by ContainerAppName
```

Compare equivalent workloads and periods when checking costs; the new schema
can change the billed bytes per record. Azure Monitor ingestion is asynchronous;
use Container Apps log streaming when immediate output is needed.

## Migration verification

1. Privately back up environment, workspace, and diagnostic configurations.
   Never commit workspace keys, Terraform state, plans, or application logs.
2. Inventory app-level diagnostic routes, workspace functions, alerts, saved
   queries, workbooks, and automation for dependencies on legacy tables.
3. Review a saved Terraform plan targeting
   `azurerm_monitor_diagnostic_setting.container_logs`. Expect one in-place environment
   logging update and one diagnostic-setting creation per deployment. Apply
   only the reviewed plan, without unrelated changes or resource replacements.
4. Emit timestamped console markers before cutover and every five minutes.
   Start with Develop where available. Immediately follow the destination
   change with the diagnostic setting (Terraform handles this dependency).
5. Verify the expected workspace and both enabled categories. Wait for at least
   two post-cutover markers with increasing timestamps; the newest must be less
   than ten minutes old before proceeding to the next environment.
6. Check the public `/api/auth/setup-status` health endpoint, application
   revision, retained historical logs, and workspace retention/billing settings.
   Verify system logs at the next natural platform event; do not restart an
   application just to generate one.

Diagnostic settings can take up to 90 minutes to activate. Monitor live streams
and health during the transition; complete log backfill is not guaranteed.
If fresh markers do not arrive within that window, investigate the route or
restore the old destination using the private workspace backup, then reconcile
Terraform before another apply. Do not delete the workspace or old tables.

Reference: [Microsoft migration guide](https://learn.microsoft.com/en-us/azure/container-apps/migrate-logs-azure-monitor).
