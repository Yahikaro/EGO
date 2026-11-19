# SQLCipher on Windows: verified package metadata, native run pending

Checked **2026-10-19**. The Windows dependency is **`sqlcipher3==0.6.4`**, a Python binding to SQLCipher. This uses the maintained SQLCipher engine rather than an encryption implementation written for EGO.

## Evidence and exact target

| Item | Verified fact |
| --- | --- |
| Official release | [PyPI sqlcipher3 0.6.4](https://pypi.org/project/sqlcipher3/0.6.4/), released 2026-10-13 |
| Target wheel | `sqlcipher3-0.6.4-cp312-cp312-win_amd64.whl` |
| Interpreter / ABI / platform | CPython 3.12 / CPython 3.12 / Windows x86-64 |
| Uploaded | 2026-10-13 |
| SHA-256 published by PyPI | `ff612c1f731a1870457a3fa4c0095bf27ce13ad7e2035ec3f98a489e2d6ccf5c` |
| Maintainer statement | [coleifer/sqlcipher3 README](https://github.com/coleifer/sqlcipher3): self-contained wheels available since 0.6.2, with SQLCipher included and no external dependencies |
| Native Windows 11 x64 execution | **NOT TESTED in this Linux workspace** |

The wheel tags establish a matching distribution for Python 3.12 x64; they do not establish successful DLL loading, encryption, Windows ACLs, DPAPI or recovery on the target PC. The recorded checksum is for this one Windows wheel, not a complete dependency lock or a native test result. Setup installs only binary wheels; it must fail if a compatible wheel is unavailable. It must not compile an unreviewed replacement or silently use ordinary SQLite.

## Installation and required local verification

On the target Windows 11 x64 PC, double-click **`tools\windows\01_SETUP.bat`** from the extracted toolkit. Use a fresh project `.venv` with 64-bit CPython 3.12. The package list is `requirements-windows.txt`, installed with `--only-binary=:all:`. Do not run `requirements-linux.lock` on Windows.

`sqlcipher3-binary` (the existing Linux development dependency) and `sqlcipher3` both provide the **same `sqlcipher3` import package**. Do not install both into one environment. A reused environment containing either conflicting distribution must be recreated or both distributions removed before a clean Windows install. Installing one over the other and then uninstalling either can leave missing or mixed files. The Linux environment used for this change was left unchanged.

Setup and **`02_ENV_CHECK.bat`** run the capability check automatically. For developers, the same check is available directly from the project root:

```text
.\.venv\Scripts\python.exe scripts\sqlcipher_check.py
```

Exit code 0 is reserved for an actual `PASS`. The checker imports the native binding only when invoked, requires a nonempty recognizable `PRAGMA cipher_version`, creates a synthetic encrypted disk database, closes it, rejects a plaintext SQLite header or visible synthetic sentinel, reopens and reads with the correct key, then forces a schema read using a different key and requires rejection. Every connection is closed; the temporary directory is removed. It never opens an application database or accepts a plaintext SQLite fallback. Keys and values are generated synthetic inputs and never enter reports.

The checker disables internal SQLCipher diagnostic logging using the documented [`PRAGMA cipher_log_level = NONE`](https://www.zetetic.net/sqlcipher/sqlcipher-api/#cipher_log_level); exceptions become fixed reason codes. Reports contain only `status`, `reason`, and `cipher_version`. A module import is safe even when SQLCipher is absent.

| Result / reason | Required interpretation |
| --- | --- |
| `PASS` / `SQLCIPHER_VERIFIED` | This local binding passed the synthetic capability probe |
| `BLOCKED` / `SQLCIPHER_NOT_INSTALLED` | Binding absent; setup/security validation must fail explicitly |
| `FAIL` / `SQLCIPHER_LOAD_FAILED` | Native import or DLL load incompatible/broken |
| `FAIL` / `SQLCIPHER_NOT_ACTIVE` | No valid SQLCipher version, including plain SQLite |
| `FAIL` / `SQLCIPHER_PLAINTEXT_DATABASE` | Synthetic disk file is empty or exposes plaintext |
| `FAIL` / `SQLCIPHER_ROUNDTRIP_FAILED` | Correct-key read did not reproduce synthetic data |
| `FAIL` / `SQLCIPHER_WRONG_KEY_ACCEPTED` | A different key still allowed a database read |
| `FAIL` / `SQLCIPHER_CHECK_FAILED` | Another check/IO/runtime failure; no raw exception disclosure |

A missing binding is a blocked capability, **not a successful skipped security test**. Setup must return failure, and the security suite must report its explicit reason rather than silently skipping critical encryption checks.

## Scope of the evidence

Local tests exercise the existing Linux `sqlcipher3-binary==0.6.0` binding (engine reports `4.12.0 community`), real encrypted create/reopen behavior, wrong-key denial, deliberate wrong-key bypass detection, plain SQLite rejection, plaintext-file detection, deferred missing/DLL import errors, safe JSON and exit codes. No Windows wheel was installed or executed here.

Temporary directories are private with mode 0700 on POSIX; Windows Python 3.12 relies on inherited per-user temporary-directory ACLs. Native ACL validation remains a target-machine task. This small checker does not certify WAL/index/temp-file scans, DPAPI, cross-account or second-PC recovery, backup portability, or production storage readiness. Run the full security suite and the target-machine checks before promoting those capabilities to PASS.
