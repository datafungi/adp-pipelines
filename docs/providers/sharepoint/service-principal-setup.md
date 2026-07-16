# SharePoint service principal setup (Sites.Selected)

Creates the Entra ID app registration `SharePointHook`
(`plugins/providers/sharepoint/hooks/sharepoint.py`) uses, and grants it access
to one specific SharePoint site.

Steps 1-3 and 5 are once per app registration. Step 4 repeats per site.

## 1. Register the app

**Entra admin center** → **Identity → Applications → App registrations → New registration**.

- Name: e.g. `adp-pipelines-sharepoint`
- Account types: **Accounts in this organizational directory only**
- No redirect URI — headless client-credentials app

Note the **Application (client) ID** and the tenant **domain** (`<tenant>.onmicrosoft.com`).

## 2. Create a certificate

Certificate auth is mandatory. A client secret cannot be made to work, and no
amount of consent changes that:

- Entra stamps app-only tokens with `appidacr` — `1` = secret, `2` = certificate.
- SharePoint REST accepts only `appidacr=2`, answering `401 Unsupported app only token`.
- Entra issues the secret-based token happily, correct audience and roles, so
  the failure surfaces only at the API and looks like a permissions problem.
- Secrets did work app-only under legacy ACS, but ACS is retired for new
  tenants and can't be scoped with `Sites.Selected` at all.

```bash
openssl req -x509 -newkey rsa:2048 -keyout private-key.pem -out certificate.pem \
  -days 730 -nodes -subj "/CN=adp-pipelines-sharepoint"
```

`-nodes` leaves the key unencrypted — the hook has no passphrase support.

1. App registration → **Certificates & secrets → Certificates → Upload certificate**.
2. Upload `certificate.pem` — the public half only, never the key.
3. Copy the **Thumbprint**.
4. Store both halves in Key Vault (this repo's `main.py` reads
   `adp-sharepoint-reader-thumbprint` / `adp-sharepoint-reader-private-key`).
5. Diarise rotation before the `-days` expiry.

## 3. Grant the `Sites.Selected` API permission

Two APIs publish a permission by this name. The hook requests a
**SharePoint**-audience token, so it needs SharePoint's, not Graph's.

1. **API permissions → Add a permission → APIs my organization uses**.
2. **Office 365 SharePoint Online** (*not* Microsoft Graph) → **Application permissions**.
3. Select **Sites.Selected** → **Add permissions**.
4. **Grant admin consent for `<tenant>`**. Mandatory — without it every call 403s.

`Sites.Selected` alone grants access to **no sites**. It only makes the app
eligible for step 4. That's the whole point of it over `Sites.Read.All`.

## 4. Grant access to a specific site

Not a portal operation — a Graph call made by a human admin, since the app has
no site access to bootstrap itself with. Easiest as a **Global Administrator**:
`POST /sites/{siteId}/permissions` needs `Sites.FullControl.All`, and consenting
to that scope is a different axis from having SharePoint authority, so
SharePoint Administrator alone can't do both halves.

**4a.** In [Graph Explorer](https://developer.microsoft.com/graph/graph-explorer),
sign in as that admin → **Modify permissions** → consent `Sites.FullControl.All`.

**4b.** Resolve the site ID:

```
GET https://graph.microsoft.com/v1.0/sites/datafungi.sharepoint.com:/sites/talentacquisition
```

Copy `id` verbatim — commas included, no URL-encoding:
`datafungi.sharepoint.com,1a2b3c4d-...,5e6f7g8h-...`

**4c.** Grant it:

```
POST https://graph.microsoft.com/v1.0/sites/{siteId}/permissions
```
```json
{
  "roles": ["read"],
  "grantedToIdentities": [
    { "application": { "id": "<client-id>", "displayName": "adp-pipelines-sharepoint" } }
  ]
}
```

Expect `201`. Record the returned permission `id` somewhere durable (e.g. the
consuming DAG's `README.md`) — it's what you'd revoke later, and there's no
portal list for these. Use `"write"` only if a DAG needs to write back.

**4d.** Verify: `GET .../permissions` should show the app's client ID with
`roles: ["read"]`.

### CLI alternative

```powershell
Connect-MgGraph -Scopes "Sites.FullControl.All"
$siteId = (Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/v1.0/sites/datafungi.sharepoint.com:/sites/talentacquisition").id
$body = @{
  roles = @("read")
  grantedToIdentities = @(@{ application = @{ id = "<client-id>"; displayName = "adp-pipelines-sharepoint" } })
} | ConvertTo-Json -Depth 5
Invoke-MgGraphRequest -Method POST -Uri "https://graph.microsoft.com/v1.0/sites/$siteId/permissions" -Body $body
```

## 5. Wire it into Airflow

All native Connection fields — no `extra` JSON. The form relabels each one:

| Form label                    | Field       | Value                                    |
|-------------------------------|-------------|------------------------------------------|
| Connection Id                 | `conn_id`   | e.g. `sharepoint_default`                |
| Connection Type               | `conn_type` | `sharepoint`                             |
| Client ID                     | `login`     | Application (client) ID, step 1          |
| Tenant                        | `host`      | `<tenant>.onmicrosoft.com`, step 1       |
| Certificate Thumbprint        | `schema`    | Thumbprint, step 2                       |
| Certificate Private Key (PEM) | `password`  | `private-key.pem` contents, pasted whole |

`schema` and `password` are repurposed — relevant only when setting the
connection outside the form (env var, API, CLI), where fields go by real name.
The key sits in `password` because Airflow masks and encrypts that field; a
custom field would render the PEM in cleartext. It is never a client secret
(step 2).

As an `AIRFLOW_CONN_*` env var it's JSON, so escape the PEM's newlines as `\n`:

```json
{"conn_type": "sharepoint", "login": "<client-id>", "host": "<tenant>.onmicrosoft.com", "schema": "<thumbprint>", "password": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"}
```

The site URL is **not** in the connection — it goes in the consuming DAG
folder's `config.toml` under `[sharepoint].site_url`. `Sites.Selected` grants
are per site, so the URL is a per-DAG concern, not a shared credential one.

## Failure modes

- **403 on the 4c POST** → `Sites.FullControl.All` not actually consented (it
  can reset per session), or the account isn't Global/SharePoint Admin.
- **404 resolving the site** → `hostname:/sites/{path}` doesn't match the
  site's server-relative URL.
- **403 at runtime** → step 3: `Sites.Selected` must show **admin consent
  granted**, and be on **Office 365 SharePoint Online**, not Graph.
- **401 at runtime** → the token isn't certificate-backed. `401` rather than
  `403` is the tell: issued then refused wholesale, versus accepted but short on
  permissions. The hook has no secret path to fall onto, so suspect the
  certificate — expired, or not the one uploaded in step 2.

Decode a token to tell these apart — `roles` proves the grant, `appidacr` the
credential type:

```python
import base64, json
payload = access_token.split(".")[1]
print(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))))
```

## Adding a site later

Repeat step 4 against the new `siteId`. The step 3 permission does not cascade.
