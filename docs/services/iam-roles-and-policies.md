# IAM Roles and Policies

## Naming Contract
IAM resource names follow:
- `${NamePrefix}-<component>-role`
- `${NamePrefix}-<component>-policy`

Full naming schema reference:
- `infra/cloudformation/docs/NAMING.md`

## Lambda Roles
- `${NamePrefix}-submit-role` / submit policy
- `${NamePrefix}-status-role` / status policy
- `${NamePrefix}-worker-role` / worker policy
- `${NamePrefix}-text2image-role` / text2image policy
- `${NamePrefix}-glb2vox-role` / glb2vox policy

Permissions are least-scope by service boundary (jobs table, specific Lambda invokes, pipeline bucket paths, Bedrock invoke, SageMaker invoke).

## SageMaker Role
- `${NamePrefix}-sagemaker-role`

Managed policies:
- `AmazonSageMakerFullAccess`
- `AmazonEC2ContainerRegistryReadOnly`

## EC2 Role + Instance Profile
- `${NamePrefix}-minecraft-role`
- `${NamePrefix}-minecraft-profile`

Policy grants read-only access to assets bucket for bootstrap/sync.

## Review Source
Exact IAM statements live in:
- `infra/cloudformation/template.yaml`
