# Infrastructure

Infrastructure as code for this asset (for example Bicep or Terraform). Parameterize everything environment-specific; never commit tenant, subscription or resource IDs. Added by `python scripts/assets.py new --with-infra`; every Bicep file must compile with `az bicep build`.
