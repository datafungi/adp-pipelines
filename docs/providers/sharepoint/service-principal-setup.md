# SharePoint service principal setup (Sites.Selected)

How to create the Entra ID app registration used by `SharePointHook`
(`plugins/providers/sharepoint/hooks/sharepoint.py`) and grant it access to a
specific SharePoint site via the `Sites.Selected` permission model. This
process is deliberately scoped so the app can only ever read the sites it's
explicitly granted, never every site in the tenant.

The app authenticates with a **certificate**, not a client secret — see step 2
for why that is not a preference but a hard SharePoint requirement.

Steps 1-3 are done once per app registration. Step 4 is repeated for every
SharePoint site a DAG needs to read from.

## 1. Register the app (create the service principal) in Entra ID

1. Go to **Entra admin center** → **Identity → Applications → App registrations → New registration**.
2. Name it something identifiable, e.g. `adp-pipelines-sharepoint`.
3. Supported account types: **Accounts in this organizational directory only** (single tenant).
4. No redirect URI needed — this is a headless client-credentials app, not interactive.
5. Click **Register**. Note down:
   - **Application (client) ID** → this becomes `login` on the Airflow Connection.
   - The tenant **domain name** (e.g. `<tenant>.onmicrosoft.com`, shown as the
     publisher/primary domain) → this becomes `host` on the Airflow Connection.

## 2. Create a certificate (not a client secret)

Entra app-only access to the SharePoint REST API **requires a certificate**. A
client secret cannot be made to work here, and this is not a configuration
mistake you can consent your way out of:

- Entra issues the token either way — correct audience, `roles:
  ["Sites.Selected"]` present — and SharePoint then rejects it with `401` at
  the API.
- The deciding claim is `appidacr`, the credential type the app authenticated
  with: `1` = client secret, `2` = certificate. SharePoint accepts app-only
  tokens only with `appidacr=2`.
- Client secrets only ever worked app-only via the legacy ACS model, which
  Microsoft has retired for new tenants and which cannot be scoped with
  `Sites.Selected` at all.

Generate a key pair (or use one from your CA):

```bash
openssl req -x509 -newkey rsa:2048 -keyout private-key.pem -out certificate.pem \
  -days 730 -nodes -subj "/CN=adp-pipelines-sharepoint"
```

`-nodes` leaves the private key unencrypted, which is what the hook expects; if
you encrypt it, the passphrase is not currently plumbed through.

1. App registration → **Certificates & secrets → Certificates → Upload certificate**.
2. Upload `certificate.pem` (the public half only — never upload the key).
3. Copy the **Thumbprint** shown after upload.
4. Store both halves in Key Vault rather than pasting them around, e.g. the
   `adp-sharepoint-reader-thumbprint` / `adp-sharepoint-reader-private-key`
   secrets this repo's `main.py` smoke test reads.
5. Set a calendar reminder to rotate before the `-days` expiry.

## 3. Grant the `Sites.Selected` API permission (application-level)

Two different APIs both publish a permission called `Sites.Selected`. The hook
requests a **SharePoint**-audience token, so it needs SharePoint's:

1. Same app registration → **API permissions → Add a permission → APIs my organization uses**.
2. Choose **Office 365 SharePoint Online** (*not* Microsoft Graph) → **Application permissions**.
3. Search for and select **Sites.Selected**.
4. Click **Add permissions**.
5. Click **Grant admin consent for `<tenant>`** (requires Global Admin or Privileged Role Admin). Mandatory — without consent, every call 403s even though the token request itself succeeds.

Graph's `Sites.Selected` is a separate grant on a separate API. The hook does
not use it; add it only if other tooling calls Graph directly with this app's
identity. Note that step 4 below *is* a Graph call, but it's made by a human
admin, not by this app.

`Sites.Selected` by itself grants access to **no sites**. It only makes the
app eligible to be granted specific sites via step 4. That's the entire
point of using it instead of `Sites.Read.All`.

## 4. Grant the app access to a specific SharePoint site

Not done in the Azure portal — it's a direct Microsoft Graph API call made
interactively by a human admin, since the app itself has no site access yet
to bootstrap this.

### Who can do this

The signed-in account needs two things at once:

1. **Actual SharePoint authority** over the site — practically, **SharePoint
   Administrator** or **Global Administrator** in the tenant. A token scoped
   with `Sites.FullControl.All` doesn't override real access; Graph checks
   both.
2. Ability to **consent** to the `Sites.FullControl.All` delegated scope for
   whatever client makes the call (Graph Explorer, PowerShell, etc.) —
   requires **Global Administrator**, **Privileged Role Administrator**,
   **Application Administrator**, or **Cloud Application Administrator**.
   `SharePoint Administrator` alone is *not* enough to consent to a Graph
   permission scope — it's a separate axis from having site authority.

Easiest path: do this as a Global Administrator.

`POST /sites/{siteId}/permissions` requires `Sites.FullControl.All`
(delegated or application) — there is no lower-privilege alternative for
this specific call.

### 4a. Get a session with `Sites.FullControl.All` consented (Graph Explorer)

1. Open <https://developer.microsoft.com/graph/graph-explorer>.
2. **Sign in** (top right) with the Global Admin account for the tenant.
3. Below the request bar, open the **Modify permissions** (or
   **Permissions**) panel.
4. Search `Sites.FullControl.All`, click **Consent** next to it.
5. Accept the consent prompt (check "Consent on behalf of your organization"
   if shown, so this doesn't need repeating next time).
6. Confirm the panel shows `Sites.FullControl.All` as consented (checkmark).

### 4b. Resolve the site's Graph `siteId`

- Method: `GET`
- URL: `https://graph.microsoft.com/v1.0/sites/{hostname}:/sites/{site-path}`
  - e.g. `https://graph.microsoft.com/v1.0/sites/datafungi.sharepoint.com:/sites/talentacquisition`
- Run it. Copy the response's `id` field verbatim, e.g.:
  ```
  datafungi.sharepoint.com,1a2b3c4d-XXXX-XXXX-XXXX-XXXXXXXXXXXX,5e6f7g8h-XXXX-XXXX-XXXX-XXXXXXXXXXXX
  ```
  Commas included, no URL-encoding needed — Graph accepts this as a single
  path segment.

### 4c. Grant the permission

- Method: `POST`
- URL: `https://graph.microsoft.com/v1.0/sites/{siteId}/permissions`
  (substitute the `id` from 4b)
- Request body:
  ```json
  {
    "roles": ["read"],
    "grantedToIdentities": [
      {
        "application": {
          "id": "<Application (client) ID from step 1>",
          "displayName": "adp-pipelines-sharepoint"
        }
      }
    ]
  }
  ```
- Run it. Expect `201 Created`. The response includes a permission `id`
  (distinct from both the site ID and the app's client ID) — record it
  somewhere durable (e.g. the consuming DAG's `README.md`), since it's what
  you'd reference to revoke or rotate the grant later, and there's no portal
  UI list for these — only the `GET` in 4d.

Use `"roles": ["write"]` only if the DAG will ever need to write back to the
site. `"read"` is the default — grant the minimum needed per site.

### 4d. Verify

- `GET https://graph.microsoft.com/v1.0/sites/{siteId}/permissions`
- The `value` array should contain an entry with
  `grantedToIdentitiesV2[].application.id` matching the app's client ID and
  `roles: ["read"]`.

### Common failure modes

- **403 on the POST** → either `Sites.FullControl.All` wasn't actually
  consented in 4a (recheck the permissions panel — it can reset per
  session), or the signed-in account isn't Global/SharePoint Admin.
- **404 resolving the site** → the `hostname:/sites/{path}` segment doesn't
  exactly match the site's server-relative URL.
- **Grant succeeds here, but the hook still gets 403 at runtime** → recheck
  step 3: the app's `Sites.Selected` *application* permission must show
  **admin consent granted** in the app registration's API permissions
  blade, not just added — and must be on **Office 365 SharePoint Online**,
  not Microsoft Graph.
- **Hook gets 401 (not 403) at runtime, with everything above correct** → the
  connection is on the client-secret branch. `401` rather than `403` is the
  tell: the token was issued and refused wholesale, rather than accepted and
  found short on permissions. Set `extra.private_key` (step 5).

To see which of these you're in, decode the token's claims — `roles` proves the
grant, `appidacr` proves the credential type:

```python
import base64, json
payload = access_token.split(".")[1]
print(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))))
```

### CLI alternative to Graph Explorer

```powershell
Connect-MgGraph -Scopes "Sites.FullControl.All"   # opens a browser for the same admin sign-in + consent as 4a
$siteId = (Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/v1.0/sites/datafungi.sharepoint.com:/sites/talentacquisition").id
$body = @{
  roles = @("read")
  grantedToIdentities = @(@{ application = @{ id = "<client-id>"; displayName = "adp-pipelines-sharepoint" } })
} | ConvertTo-Json -Depth 5
Invoke-MgGraphRequest -Method POST -Uri "https://graph.microsoft.com/v1.0/sites/$siteId/permissions" -Body $body
```

## 5. Wire it into Airflow

Create the Airflow Connection matching what `SharePointHook` expects:

- `conn_id`: e.g. `sharepoint_default`
- `conn_type`: `sharepoint`
- `login` (**Client ID**): Application (client) ID from step 1
- `host` (**Tenant**): tenant domain name from step 1, e.g. `<tenant>.onmicrosoft.com`
- `extra`:
  ```json
  {
    "thumbprint": "<thumbprint from step 2>",
    "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
  }
  ```
  The PEM is multi-line, so its newlines must be `\n`-escaped to stay valid
  JSON. Leave **Client Secret** empty.

`extra.private_key` is what selects certificate auth — the hook falls back to
its client-secret branch when it's absent, and that branch cannot authenticate
against SharePoint (step 2). It is retained only for a possible future
Graph-based hook, where `appidacr=1` *is* accepted.

The site URL itself does **not** go in the Connection — it belongs in the
consuming DAG folder's `config.toml`, under `[sharepoint].site_url`. Sites
Selected grants are issued per site anyway, so the URL is inherently a
per-DAG concern, not a shared credential concern.

## Adding a new site later

If a DAG needs to read from an additional SharePoint site, repeat step 4
against that site's `siteId`. The `Sites.Selected` application permission
from step 3 does not cascade to new sites automatically — each site needs
its own explicit grant.
