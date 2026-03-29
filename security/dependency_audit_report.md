# Dependency Security Audit Report

## Direct dependencies (requirements.txt)

| Package | Version | Criticality |
|---|---:|---|
| `alembic` | `1.16.1` | General |
| `blinker` | `1.9.0` | General |
| `cachelib` | `0.9.0` | General |
| `cachetools` | `5.5.0` | General |
| `certifi` | `2024.12.14` | General |
| `chardet` | `5.2.0` | General |
| `charset-normalizer` | `3.4.0` | General |
| `click` | `8.1.7` | General |
| `cloudinary` | `1.42.2` | General |
| `colorama` | `0.4.6` | General |
| `cryptography` | `46.0.5` | Crypto |
| `defusedxml` | `0.7.1` | General |
| `dnspython` | `2.7.0` | General |
| `email_validator` | `2.2.0` | General |
| `et_xmlfile` | `2.0.0` | General |
| `Flask` | `3.1.3` | Web/Auth Session |
| `Flask-Caching` | `2.3.0` | General |
| `Flask-Login` | `0.6.3` | Auth |
| `Flask-Mail` | `0.10.0` | General |
| `Flask-Migrate` | `4.1.0` | General |
| `flask-paginate` | `2024.4.12` | General |
| `Flask-SQLAlchemy` | `3.0.3` | General |
| `Flask-WTF` | `1.2.2` | Auth/CSRF |
| `fonttools` | `4.60.2` | General |
| `fpdf2` | `2.8.2` | General |
| `git-filter-repo` | `2.47.0` | General |
| `google-api-core` | `2.24.0` | General |
| `google-api-python-client` | `2.156.0` | General |
| `google-auth` | `2.37.0` | General |
| `google-auth-httplib2` | `0.2.0` | General |
| `google-auth-oauthlib` | `1.2.1` | General |
| `googleapis-common-protos` | `1.66.0` | General |
| `greenlet` | `3.2.2` | General |
| `gspread` | `6.1.4` | General |
| `gunicorn` | `23.0.0` | General |
| `httplib2` | `0.22.0` | General |
| `idna` | `3.10` | General |
| `iniconfig` | `2.1.0` | General |
| `itsdangerous` | `2.2.0` | Session signing |
| `Jinja2` | `3.1.6` | Template engine |
| `Mako` | `1.3.10` | General |
| `MarkupSafe` | `3.0.2` | General |
| `numpy` | `2.0.2` | General |
| `oauth2client` | `4.1.3` | General |
| `oauthlib` | `3.2.2` | General |
| `openpyxl` | `3.1.5` | General |
| `packaging` | `24.2` | General |
| `pandas` | `2.2.3` | General |
| `pillow` | `11.3.0` | General |
| `pluggy` | `1.6.0` | General |
| `proto-plus` | `1.25.0` | General |
| `protobuf` | `5.29.6` | Data serialization |
| `pyasn1` | `0.6.3` | General |
| `pyasn1_modules` | `0.4.1` | General |
| `pyparsing` | `3.2.0` | General |
| `pytest` | `7.4.0` | General |
| `python-dateutil` | `2.9.0.post0` | General |
| `python-dotenv` | `1.0.1` | Secrets/config |
| `qrcode` | `8.2` | General |
| `pytz` | `2024.2` | General |
| `RapidFuzz` | `3.11.0` | General |
| `reportlab` | `4.2.5` | General |
| `requests` | `2.32.4` | HTTP client |
| `requests-oauthlib` | `2.0.0` | General |
| `rsa` | `4.9` | General |
| `six` | `1.17.0` | General |
| `SQLAlchemy` | `2.0.41` | DB ORM |
| `tqdm` | `4.67.1` | General |
| `typing_extensions` | `4.13.2` | General |
| `tzdata` | `2024.2` | General |
| `uritemplate` | `4.1.1` | General |
| `urllib3` | `2.6.3` | HTTP transport |
| `waitress` | `3.0.2` | General |
| `Werkzeug` | `3.1.6` | Request/security core |
| `WTForms` | `3.2.1` | General |
| `psycopg[binary]` | `3.2.9` | DB driver |
| `psycopg2-binary` | `2.9.9` | DB driver |

## pip-audit baseline (before remediation)

- Vulnerabilities: **21** in **10** packages
- `cryptography==45.0.4`: 1 findings (GHSA-r6ph-v2qm-q3c2)
- `flask==3.1.0`: 2 findings (GHSA-4grg-w6v8-c28g, GHSA-68rp-wp8r-4726)
- `fonttools==4.56.0`: 1 findings (GHSA-768j-98cg-p3fv)
- `protobuf==5.29.2`: 2 findings (GHSA-8qvm-5x2c-j2w7, GHSA-7gcm-g887-7qv7)
- `requests==2.32.3`: 1 findings (GHSA-9hjg-9r4m-mvj7)
- `urllib3==2.2.3`: 5 findings (GHSA-48p4-8xcf-vxj5, GHSA-pq67-6m6q-mj2v, GHSA-gm62-xv2j-4w53, GHSA-2xpw-w6gg-jr37, GHSA-38jv-5279-wg99)
- `jinja2==3.1.4`: 3 findings (GHSA-q2x7-8rv6-6q7h, GHSA-gmj6-6f8f-6699, GHSA-cpwx-vrp4-4pq7)
- `pillow==11.0.0`: 1 findings (GHSA-cfh3-3jmp-rvhc)
- `pyasn1==0.6.1`: 2 findings (GHSA-63vm-454h-vhhq, GHSA-jr27-m4p2-rc6r)
- `werkzeug==3.1.3`: 3 findings (GHSA-hgf8-39gv-g3f2, GHSA-87hc-h4r5-73f7, GHSA-29vq-49wr-vm6x)

## pip-audit current (after remediation)

- Vulnerabilities: **1** in **1** package
- `pillow==11.3.0`: 1 finding (GHSA-cfh3-3jmp-rvhc)

## safety check (optional cross-check)

- Report: `security/reports/safety-after.json`
- Findings reported: 5 (`protobuf`, `pillow`, `fpdf2`, `fonttools`, `flask-caching`)
- Note: `safety` currently reports findings without fixed versions/severity metadata in this environment; remediation decisions were driven by `pip-audit` + compatibility validation.

## Accepted residual risk

- `pillow` remains with `GHSA-cfh3-3jmp-rvhc` because the fixed version (`12.1.1`) requires Python >= 3.10; this project is currently on Python 3.9.
- Temporary allow-entry documented in `security/pip_audit_ignore.txt`.

## Supply-chain controls implemented

- Strict pinning validation: `scripts/security/validate_dependency_policy.py`
- Approved package allowlist: `security/dependency_allowlist.txt`
- Denylist against known typosquatting names: `security/dependency_denylist.txt`
- CI vulnerability gate: `scripts/security/pip_audit_gate.py` + `.github/workflows/dependency-security.yml`
- SBOM generation: `security/reports/sbom.cyclonedx.json`
- Dependency tree export: `security/reports/pipdeptree-after.txt`
