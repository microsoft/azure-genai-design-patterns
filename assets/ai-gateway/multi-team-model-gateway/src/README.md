# Client source: calling the shared model as a team

> **Status: reference client code.** It runs against a gateway deployed with [../infra/](../infra/README.md). It has been compiled with `python -m py_compile`; run it in your own sandbox before you adopt it.

This folder shows how a team's application gets to the shared model with **no key in its configuration**:

1. The app signs in to **its own team's Foundry project** with Microsoft Entra ID (`DefaultAzureCredential`).
2. It reads the project connection `ai-gateway-team-<team>` **with credentials**. That connection holds the team's API Management subscription key and the gateway endpoint.
3. It creates an `AzureOpenAI` client that targets the gateway with that key. The gateway counts the call against the team's budget, removes the key and calls the model with its own managed identity.

A member of team alpha cannot run step 2 against team beta's project, because alpha has no role there. That is the project isolation in this design.

## Files

| File | Purpose |
|---|---|
| `team_client.py` | Library. `TeamSettings.from_env()` reads the non-secret settings. `read_gateway_endpoint()` reads the connection. `build_openai_client()` creates the client. `TeamGatewayClient.chat()` returns the text and the gateway budget headers, and re-reads the connection once after a 401 so a rotated key is picked up. `list_project_connections()` lists connections without reading credentials. |
| `run_team.py` | CLI: one chat call as a team, printing the `x-tokens-consumed` and `x-remaining-tokens` headers. `--list-connections` shows what the team's project contains. |
| `tpm_burst_test.py` | Isolation proof. Sends requests as one team until the gateway throttles it, checks that the 429 came from the token limit rather than model capacity, then calls as a second team and expects success. |
| `requirements.txt` | `azure-ai-projects`, `azure-identity`, `openai`, `python-dotenv`. |
| `.env.sample` | Project endpoints, connection names, deployment and API version. No keys. |

## Setup

```bash
cd assets/ai-gateway/multi-team-model-gateway/src
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.sample .env                # .env is git-ignored; keep it that way
az login                           # or run under a managed identity
```

Fill in `.env` from the deployment outputs:

```bash
az deployment group show -g <resource-group> -n multi-team-model-gateway \
  --query "properties.outputs.teams.value[].{team:team, endpoint:projectEndpoint, connection:connectionName}" -o table
```

| Variable | Source | Example |
|---|---|---|
| `TEAM_<TEAM>_PROJECT_ENDPOINT` | `teams[].projectEndpoint` output | `https://aif-contoso.services.ai.azure.com/api/projects/team-alpha` |
| `TEAM_<TEAM>_CONNECTION` | `teams[].connectionName` output | `ai-gateway-team-alpha` (this is also the default) |
| `MODEL_DEPLOYMENT` | `modelDeploymentName` output | `chat` |
| `OPENAI_API_VERSION` | An Azure OpenAI data-plane API version the gateway forwards | `2024-10-21` |

`<TEAM>` is the team name in upper case, with hyphens replaced by underscores.

## Run

```bash
# One call as team alpha (you must be a member of the alpha group).
python run_team.py --team alpha

# Show what is in the team project; no credentials are read.
python run_team.py --team alpha --list-connections

# Try to use another team's connection. Expect "access denied" (exit code 3)
# unless you are also a member of beta.
python run_team.py --team beta

# Isolation proof. Run as an operator who is a member of both projects, in a
# sandbox where the alpha budget is small.
python tpm_burst_test.py --burst-team alpha --control-team beta
```

### Exit codes

| Script | Code | Meaning |
|---|---|---|
| `run_team.py` | 0 | Call succeeded. |
| | 1 | Gateway or connection problem other than access or throttling. |
| | 2 | Missing settings. |
| | 3 | You have no access to that team's project. |
| | 4 | The team is over its token budget; the message includes `retry-after`. |
| `tpm_burst_test.py` | 0 | PASS: the burst team was throttled by the gateway and the control team was still served. |
| | 1 | Inconclusive: no throttle, or the 429 came from model capacity rather than the team budget. |
| | 2 | FAIL: the control team was refused too. Check that each team has its own product. |
| | 3 | Setup problem. |

## Using the pattern in your own app

```python
from team_client import TeamGatewayClient, TeamSettings

client = TeamGatewayClient(TeamSettings.from_env("alpha"))
result = client.chat("Draft a release note for the new search filter.")
print(result.text, result.gateway_headers)
```

For other SDK surfaces the gateway exposes, such as streaming or embeddings, call `read_gateway_endpoint()` and `build_openai_client()` and use the returned `AzureOpenAI` client directly. For streaming chat, set `stream_options={"include_usage": True}` so the gateway's token metric records completion tokens.

Guidelines for production code:

- **Read the connection at startup and after a 401.** Do not read it for every request.
- **Never log the key.** `GatewayEndpoint` hides it in `repr`; keep it that way.
- **Honour `retry-after`.** On a 429 from the gateway, back off for the advertised interval. Do not hammer the gateway or spread load across another team's key.
- **Treat `x-team` as diagnostic.** The gateway counts tokens by product (that is, by key) and removes `x-team` before it calls the model. Sending another team's name in `x-team` changes nothing.
- **Run under a managed identity in Azure.** Give that identity Foundry User on its team's project only, the same as a human member.

## Verify in your tenant

- **Credential reads by Foundry User.** `connections.get(..., include_credentials=True)` returned the key to a Foundry User on the project in the proof of concept behind this design. Confirm the role grants that action in your tenant.
- **SDK versions.** The `azure-ai-projects` connection API changed between preview and GA releases. The code expects the 1.x GA surface, where `connection.credentials.api_key` holds the key and `connection.target` holds the endpoint.
- **429 classification.** `tpm_burst_test.classify_429()` tells gateway throttles from model capacity throttles by the response body shape. Check one of each in your gateway logs before you trust the classification.
