# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately through
[GitHub private vulnerability reporting](https://github.com/lambdadevelopment/lambda-erp/security/advisories/new).
Do not include exploit details, credentials, or customer data in public issues,
pull requests, or discussions.

Include the affected version or commit, the required access level, expected and
actual behavior, and a minimal reproduction using synthetic data. Please avoid
testing against systems or data you do not own or have permission to test.

We will coordinate verification, remediation, and disclosure with the reporter.
Please keep details private while we work on a fix and agree on publication.
We welcome independent verification of proposed patches and will coordinate
credit with the reporter.

## Supported versions

Use the latest stable release for security fixes. A maintenance backport may be
provided when necessary; older versions do not receive security updates unless
an explicit patched release is published for them. Update both the backend
package and the corresponding frontend package when upgrading a normal release.

## Deployments

The public demo intentionally permits limited document writes. Production
deployments should disable public demo access and grant users only the roles
they need. A viewer role does not grant permission to change business records
or post accounting entries, regardless of whether access is through REST, MCP,
or chat.
