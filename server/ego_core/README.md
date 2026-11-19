# EGO Server Core — M1.3

service.py owns one persistent runtime, async preload and guarded STOPPED/STARTING/LOADING/READY/BUSY/STOPPING/ERROR states. One generation at a time; completion and soft cancel retain the model. Loading changes/explicit restart replace it. Close reaps owned work before SQLCipher stores close.

control.py/daemon.py provide authenticated loopback HTTPS, installation preflight, process-owned lease, startup STOP/EOF handoff and Windows Job Object / POSIX session ownership. An observed PID/name grants no right to terminate. keys.py retains DPAPI independent keys/token; store.py retains schema v1 and separate encrypted main/profile domains. Generation gets MainStore only.

See ../../docs/M1_3_PERSISTENT_RUNTIME.md, M1_API.md, M1_3_REPORT.md and WINDOWS_M1_3_CHECKLIST.md. Linux tests use real SQLCipher/TLS/disposable workers and test-only DPAPI injection. Native Windows acceptance is pending.
