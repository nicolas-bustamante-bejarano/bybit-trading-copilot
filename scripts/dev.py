from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time


def stream(name: str, process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        print(f"[{name}] {line}", end="", flush=True)


def stop(processes: list[subprocess.Popen[str]]) -> None:
    for process in processes:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(p.poll() is None for p in processes):
        time.sleep(0.05)
    for process in processes:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)


def main() -> int:
    api_port = os.environ.get("DEV_API_PORT", "8000")
    web_port = os.environ.get("DEV_WEB_PORT", "3000")
    environment = os.environ.copy()
    environment.setdefault("COPILOT_AUTH_DISABLED", "true")
    environment.setdefault("COPILOT_API_BASE_URL", f"http://127.0.0.1:{api_port}")
    commands = [
        (
            "api",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "trading_copilot.main:app",
                "--reload",
                "--port",
                api_port,
            ],
        ),
        ("web", ["npm", "--prefix", "web", "run", "dev", "--", "--port", web_port]),
    ]
    processes = [
        subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
            env=environment,
        )
        for _, command in commands
    ]
    for (name, _), process in zip(commands, processes, strict=True):
        threading.Thread(target=stream, args=(name, process), daemon=True).start()
    try:
        while all(process.poll() is None for process in processes):
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        stop(processes)
    return next((process.returncode or 1 for process in processes if process.returncode), 0)


if __name__ == "__main__":
    raise SystemExit(main())
