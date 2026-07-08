"""Sage Local Runner — launches the web UI (API + frontend)."""

from __future__ import annotations

import atexit
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LOCAL_DIRS = {
    "offline demo": ROOT / ".local" / "demo",
    "live demo": ROOT / ".local" / "demo-live",
    "interactive": ROOT / ".local" / "interactive",
    "ui (api/web)": ROOT / ".local" / "ui",
}

API_PORT = 8000
FRONTEND_PORT = 3000


def _clear_service_ports() -> None:
    """Best-effort free of API/frontend ports (surviving grandchildren)."""
    try:
        kill_port(API_PORT)
    except Exception:
        pass
    try:
        kill_port(FRONTEND_PORT)
    except Exception:
        pass


def _stop_tree(proc: subprocess.Popen, *, timeout: float = 8.0) -> int:
    """Terminate the whole process tree rooted at proc, waiting for exit.

    Windows: ``taskkill /F /T /PID <pid>`` (kills entire descendant tree),
    then always clears API/frontend ports so reparented grandchildren die.
    POSIX:   kill the process group with SIGTERM, then SIGKILL if needed
             (relies on children started with start_new_session=True).

    Returns the proc's returncode (or -signal if killed by signal).
    Never raises: safe to call from atexit / signal handlers.
    """
    try:
        rc = proc.poll()
    except Exception:
        rc = None

    # Even if the direct child already exited, grandchildren may have been
    # reparented (uv/cmd wrappers). Always clear service ports on Windows.
    if rc is not None:
        if sys.platform == "win32":
            _clear_service_ports()
        try:
            return proc.returncode if proc.returncode is not None else 0
        except Exception:
            return 0

    try:
        if sys.platform == "win32":
            # /T = whole descendant subtree rooted at PID. Reaches python under uv.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                shell=False,
            )
            _clear_service_ports()
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    shell=False,
                )
                _clear_service_ports()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            else:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
    except Exception as exc:  # never raise out of atexit/signal context
        try:
            print(f"_stop_tree: error during tree kill: {exc!r}", file=sys.stderr)
        except Exception:
            pass
        if sys.platform == "win32":
            _clear_service_ports()

    try:
        return proc.returncode if proc.returncode is not None else 0
    except Exception:
        return 0


class _JobGuard:
    """Win32 Job Object orphan protection (no-op on POSIX).

    Assigns each child's PID to a Job Object flagged
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE so that if the parent python
    dies (crash, hard-kill, terminal closed) the kernel reaps the whole
    child tree when the job HANDLE is closed on interpreter exit.

    Descendants inherit job membership automatically. Do not enable
    BREAKAWAY_OK — that lets uv/npm children escape the job.
    """

    def __init__(self) -> None:
        self._handle = None  # keep alive for life of launch_web_ui
        self._procs: list[subprocess.Popen] = []
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            self._ctypes = ctypes
            self._wintypes = wintypes

            kernel32 = ctypes.windll.kernel32

            # Must not share a name with the Structure class below (name-shadow
            # previously passed a type object where an int info-class is required).
            JobObjectExtendedLimitInformation = 9
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_ulonglong),
                    ("WriteOperationCount", ctypes.c_ulonglong),
                    ("OtherOperationCount", ctypes.c_ulonglong),
                    ("ReadTransferCount", ctypes.c_ulonglong),
                    ("WriteTransferCount", ctypes.c_ulonglong),
                    ("OtherTransferCount", ctypes.c_ulonglong),
                ]

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_void_p),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION_STRUCT(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            kernel32.SetInformationJobObject.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            kernel32.SetInformationJobObject.restype = wintypes.BOOL
            kernel32.AssignProcessToJobObject.argtypes = [
                wintypes.HANDLE,
                wintypes.HANDLE,
            ]
            kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
            kernel32.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.DuplicateHandle.argtypes = [
                wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                wintypes.BOOL, wintypes.DWORD,
            ]
            kernel32.DuplicateHandle.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL

            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                raise ctypes.WinError()
            self._handle = job

            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION_STRUCT()
            info.BasicLimitInformation.LimitFlags = (
                JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            ok = kernel32.SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not ok:
                raise ctypes.WinError()
        except Exception as exc:
            try:
                print(f"_JobGuard: initialization failed: {exc!r}", file=sys.stderr)
            except Exception:
                pass
            # Tear down a half-created job handle so we do not leak it.
            try:
                if self._handle:
                    import ctypes as _ct

                    _ct.windll.kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None

    def add(self, proc: subprocess.Popen) -> None:
        if sys.platform != "win32" or self._handle is None:
            return
        try:
            ctypes = self._ctypes
            wintypes = self._wintypes
            kernel32 = ctypes.windll.kernel32

            PROCESS_SET_QUOTA = 0x0100
            PROCESS_TERMINATE = 0x0001
            PROCESS_SET_INFORMATION = 0x0200
            access = (
                PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_SET_INFORMATION
            )

            # Prefer OpenProcess by PID; fall back to Popen's OS handle.
            h = kernel32.OpenProcess(access, False, proc.pid)
            opened = bool(h)
            if not h:
                h = getattr(proc, "_handle", None)
            if not h:
                raise OSError(f"cannot open process handle for pid={proc.pid}")

            dup = wintypes.HANDLE()
            cur_proc = kernel32.GetCurrentProcess()
            ok = kernel32.DuplicateHandle(
                cur_proc,
                h,
                cur_proc,
                ctypes.byref(dup),
                0,
                False,
                0x0002,  # DUPLICATE_SAME_ACCESS
            )
            if not ok:
                # retry using the original Popen handle if different
                ph = getattr(proc, "_handle", None)
                if ph and ph is not h:
                    h = ph
                    opened = False
                    ok = kernel32.DuplicateHandle(
                        cur_proc,
                        h,
                        cur_proc,
                        ctypes.byref(dup),
                        0,
                        False,
                        0x0002,
                    )
            if not ok:
                raise ctypes.WinError()

            assign_ok = bool(kernel32.AssignProcessToJobObject(self._handle, dup))
            if not assign_ok:
                # ERROR_ACCESS_DENIED (5) means child is already in another job
                # (e.g. if launched under a parent job). Layer 1 still covers
                # the graceful path; do not crash.
                err = kernel32.GetLastError()
                try:
                    print(
                        f"_JobGuard: AssignProcessToJobObject failed "
                        f"(err={err}); relying on _stop_tree",
                        file=sys.stderr,
                    )
                except Exception:
                    pass
            self._procs.append(proc)
            # Close the duplicated handle we no longer need; assignment keeps a ref.
            try:
                kernel32.CloseHandle(dup)
            except Exception:
                pass
            if opened:
                try:
                    kernel32.CloseHandle(h)
                except Exception:
                    pass
        except Exception as exc:
            try:
                print(f"_JobGuard.add: error: {exc!r}", file=sys.stderr)
            except Exception:
                pass

    def release(self) -> None:
        if sys.platform != "win32" or self._handle is None:
            return
        try:
            ctypes = self._ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.CloseHandle(self._handle)
        except Exception:
            pass
        self._handle = None

    @property
    def active(self) -> bool:
        return self._handle is not None


def pause() -> None:
    try:
        input("\nPress Enter to continue...")
    except (EOFError, KeyboardInterrupt):
        print()


def run(command: list[str], *, require_key: bool = False) -> int:
    if require_key and not os.environ.get("SAGE_QWEN_API_KEY"):
        print("\nSAGE_QWEN_API_KEY is not set.")
        print("Set it first, then rerun this option.")
        print("PowerShell: $env:SAGE_QWEN_API_KEY='your-key'")
        print("bash/zsh:    export SAGE_QWEN_API_KEY='your-key'")
        return 2

    print("\n$ " + " ".join(command))
    try:
        completed = subprocess.run(command, cwd=ROOT)
    except FileNotFoundError as exc:
        missing = command[0]
        print(f"\nCould not find '{missing}'. Install uv and make sure it is on PATH.")
        print(f"Original error: {exc}")
        return 127
    return completed.returncode


def clean_local_memory() -> int:
    existing = [path for path in LOCAL_DIRS.values() if path.exists()]
    if not existing:
        print("\nNo local wrapper memory directories exist.")
        return 0

    print("\nThis will remove only these wrapper-created directories:")
    for path in existing:
        print(f"  {path.relative_to(ROOT)}")

    try:
        answer = input("Delete them? Type 'yes' to continue: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled (non-interactive).")
        return 1
    if answer != "yes":
        print("Cancelled.")
        return 1

    for path in existing:
        resolved = path.resolve()
        local_root = (ROOT / ".local").resolve()
        if local_root not in resolved.parents and resolved != local_root:
            print(f"Refusing to delete unexpected path: {resolved}")
            return 3
        shutil.rmtree(resolved)

    print("Removed local wrapper memory directories.")
    return 0


def kill_port(port: int) -> None:
    """Kill any process tree occupying the given port."""
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                shell=False,
            )
            seen: set[str] = set()
            for line in result.stdout.splitlines():
                # Match LISTENING rows for this port (avoid ESTABLISHED noise).
                if f":{port}" not in line or "LISTENING" not in line.upper():
                    continue
                parts = line.split()
                if not parts:
                    continue
                pid = parts[-1]
                if not pid.isdigit() or pid in seen or pid == "0":
                    continue
                seen.add(pid)
                # /T reaps node/esbuild children under the listener.
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", pid],
                    capture_output=True,
                    shell=False,
                )
                print(f"Killed process tree on port {port} (PID {pid})")
        except Exception:
            pass
    else:
        try:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True,
                text=True,
            )
            if result.stdout.strip():
                for pid in result.stdout.strip().split():
                    subprocess.run(["kill", "-9", pid], capture_output=True)
                    print(f"Killed process on port {port} (PID {pid})")
        except Exception:
            pass


def _api_command() -> list[str]:
    """Prefer project venv python (shallower tree) over ``uv run`` wrapper."""
    if sys.platform == "win32":
        venv_py = ROOT / ".venv" / "Scripts" / "python.exe"
    else:
        venv_py = ROOT / ".venv" / "bin" / "python"
    if venv_py.is_file():
        return [str(venv_py), "api.py"]
    return ["uv", "run", "python", "api.py"]


def launch_web_ui() -> int:
    frontend_dir = ROOT / "frontend"
    node_modules = frontend_dir / "node_modules"
    use_shell = sys.platform == "win32"

    if not node_modules.exists():
        print("\nInstalling frontend dependencies...")
        result = subprocess.run(["npm", "install"], cwd=frontend_dir, shell=use_shell)
        if result.returncode != 0:
            print("Failed to install frontend dependencies.")
            return result.returncode

    print(f"\nClearing ports {API_PORT} and {FRONTEND_PORT}...")
    kill_port(API_PORT)
    kill_port(FRONTEND_PORT)

    print(f"\nStarting API server on port {API_PORT}...")
    popen_kwargs: dict = {"cwd": ROOT}
    if sys.platform == "win32":
        # New process group so taskkill /T rooted at this PID is well-defined.
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    api_cmd = _api_command()
    print(f"  cmd: {api_cmd}")
    api_proc = subprocess.Popen(api_cmd, **popen_kwargs)

    print(f"Starting frontend dev server on port {FRONTEND_PORT}...")
    frontend_kwargs: dict = {"cwd": frontend_dir}
    if sys.platform == "win32":
        # Avoid shell=True (extra cmd.exe that reparents poorly). npm.cmd is fine.
        frontend_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        npm = shutil.which("npm.cmd") or shutil.which("npm") or "npm.cmd"
        frontend_cmd = [npm, "run", "dev"]
    else:
        frontend_kwargs["start_new_session"] = True
        frontend_cmd = ["npm", "run", "dev"]
    frontend_proc = subprocess.Popen(frontend_cmd, **frontend_kwargs)

    # Layer 2 — kernel orphan protection for hard-kill / crash cases.
    job = _JobGuard()
    job.add(api_proc)
    job.add(frontend_proc)
    if sys.platform == "win32" and not job.active:
        print(
            "WARNING: Job Object protection inactive; relying on tree-kill + ports.",
            file=sys.stderr,
        )

    # Layer 3 — atexit + signal belt-and-suspenders.
    # Keep default SIGINT/KeyboardInterrupt behavior; add atexit + SIGBREAK
    # (Windows) / SIGTERM,SIGHUP (POSIX). Idempotent cleanup is required.
    cleaned = {"done": False}

    def _cleanup(*_):
        if cleaned["done"]:
            return
        cleaned["done"] = True
        _stop_tree(api_proc)
        _stop_tree(frontend_proc)
        _clear_service_ports()

    atexit.register(_cleanup)
    if sys.platform == "win32":
        try:
            signal.signal(signal.SIGBREAK, _cleanup)
        except (ValueError, OSError):
            pass  # not in main thread / unsupported
    else:
        for s in (signal.SIGTERM, signal.SIGHUP):
            try:
                signal.signal(s, _cleanup)
            except (ValueError, OSError):
                pass

    launched = {"job": job}

    print("\nSage is running:")
    print(f"  Web UI:  http://localhost:{FRONTEND_PORT}")
    print(f"  API:     http://localhost:{API_PORT}/docs")
    if job.active:
        print("  Process guard: Job Object (kill-on-close) active")
    print("\nPress Ctrl+C to stop both servers.\n")

    try:
        while True:
            if api_proc.poll() is not None:
                print(f"\nAPI server exited with code {api_proc.returncode}")
                _cleanup()
                _release_job(launched["job"])
                return api_proc.returncode if api_proc.returncode is not None else 0
            if frontend_proc.poll() is not None:
                print(f"\nFrontend server exited with code {frontend_proc.returncode}")
                _cleanup()
                _release_job(launched["job"])
                return frontend_proc.returncode if frontend_proc.returncode is not None else 0
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        _cleanup()
        print("Both servers stopped.")
        _release_job(launched["job"])
        return 0


def _release_job(job: "_JobGuard") -> None:
    """Close the Job Object handle AFTER _stop_tree so the graceful path
    (Layer 1) runs to completion before the kernel reaps the tree."""
    job.release()


def menu() -> None:
    print(
        """
Sage Local Runner

1. Setup dependencies
2. Run tests
3. Launch web UI
4. Clean wrapper local memory
0. Exit
""".strip()
    )


def main() -> int:
    actions = {
        "1": lambda: run(["uv", "sync", "--all-groups"]),
        "2": lambda: run(["uv", "run", "pytest", "-q"]),
        "3": launch_web_ui,
        "4": clean_local_memory,
    }

    while True:
        _ = subprocess.run('cls', shell=True)
        menu()
        try:
            choice = input("\nChoose an option: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting (non-interactive input detected).")
            return 0
        if choice == "0":
            return 0

        action = actions.get(choice)
        if action is None:
            print("Unknown option.")
            pause()
            continue

        code = action()
        print(f"\nExit code: {code}")
        pause()


if __name__ == "__main__":
    raise SystemExit(main())
