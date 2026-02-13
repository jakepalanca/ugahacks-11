# API Gateway: CreateBuild HTTP API

## Purpose
Public API ingress from Minecraft plugin to Lambda backend.

## CloudFormation Resources
- API name pattern: `${NamePrefix}-http-api`
- Stage: `${ApiStageName}` (default `prod`)

## Routes
- `POST /build` -> submit Lambda integration
- `GET /build/status/{jobId}` -> status Lambda integration

## Auth Model
- API Gateway itself is not using authorizers in the template.
- Token enforcement is implemented inside submit/status Lambdas via `API_TOKEN` (required by default).
- You can bypass this only for testing by setting CloudFormation parameter `AllowUnauthenticatedApi=true` (which sets Lambda env `ALLOW_UNAUTHENTICATED_REQUESTS=1`).

## URL Contract
Computed in EC2 user-data and plugin config:
- submit: `https://<api-id>.execute-api.<region>.amazonaws.com/<stage>/build`
- status: `https://<api-id>.execute-api.<region>.amazonaws.com/<stage>/build/status`
