# Device credential lifecycle (backend-enforced)

Every Pi authenticates with `X-Device-ID` + `X-Api-Key`. The cloud verifies the
secret against the device's single **active** row in `pi_device_credentials`
(SHA-256 verifier of a 256-bit random secret, constant-time compare). The
`pi_devices.api_key_hash` column is no longer consulted for authentication.

| Operation | Endpoint (super admin) | Effect |
|---|---|---|
| Create device | `POST /api/super-admin/devices/save` | issues credential, returns `api_key` **once** |
| Rotate | `POST /api/super-admin/devices/{id}/credentials/rotate` | revokes active row + inserts new one in one transaction; returns new `api_key` **once** |
| Revoke | `POST /api/super-admin/devices/{id}/credentials/revoke` | blocks the device immediately; society/device/history untouched; idempotent |
| Retire device / society | existing delete/retire endpoints | also revokes active credentials (`DEVICE_RETIRED` / `SOCIETY_RETIRED`) |
| Inspect | `GET /api/super-admin/devices/{id}/credentials` | key_id/status/origin/timestamps only — never verifiers |

Audit: `CREDENTIAL_CREATED`, `CREDENTIAL_ROTATED`, `CREDENTIAL_REVOKED` rows in
`audit_log` (key_ids only, never the secret). Ordinary Pi syncs write nothing to
the credential or audit tables (no last-used tracking).

## Provisioning the Pi (limitation, by design)

The firmware reads `EMS_DEVICE_ID` / `EMS_API_KEY` from its environment
(`config.py`, provisioned by `setup_pi.sh` / the systemd unit). Rotation is
**out-of-band**: after rotating in the cloud, update `EMS_API_KEY` on the Pi and
restart the service. The old secret stops working the moment the rotation
commits, so plan the update as one maintenance step. No credential is ever
persisted by the firmware itself (0 flash writes), and no in-band key delivery
protocol is implemented in this phase.
