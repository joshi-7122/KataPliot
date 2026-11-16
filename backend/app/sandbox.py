"""Sandboxed code execution. Generated code is NEVER run directly on the host.

Backends (chosen by SANDBOX_BACKEND, "auto" picks the first available):
  docker   - throwaway containers: --network none, memory/cpu/pids limits, read-only rootfs.
  seatbelt - macOS sandbox-exec profile: no network, no reads under /Users (your home), writes only
             inside a throwaway work dir, CPU/file-size rlimits, wall-clock kill.
  judge0   - remote Judge0 instance (self-hosted or RapidAPI).
  none     - execution disabled; validation reports "inconclusive".
"""
from __future__ import annotations

import asyncio
import os
import platform
import re
import resource
import shutil
import signal
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .config import Settings

LANG_FILES = {"cpp": "main.cpp", "c": "main.c", "python": "main.py", "javascript": "main.js"}

BITS_STDCXX = """// Shim so GCC-style `#include <bits/stdc++.h>` also compiles under clang/libc++.
#pragma once
#include <algorithm>
#include <array>
#include <bitset>
#include <cassert>
#include <cctype>
#include <climits>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <functional>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <limits>
#include <list>
#include <map>
#include <memory>
#include <numeric>
#include <queue>
#include <random>
#include <set>
#include <sstream>
#include <stack>
#include <string>
#include <tuple>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>
"""


@dataclass
class RunOutcome:
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    time_s: float = 0.0


@dataclass
class ExecResult:
    compiled: bool = True
    compile_output: str = ""
    runs: list[RunOutcome] = field(default_factory=list)
    backend: str = "none"
    error: str | None = None


class SandboxUnavailable(RuntimeError):
    pass


def java_class_name(code: str) -> str:
    m = re.search(r"public\s+(?:final\s+)?class\s+([A-Za-z_]\w*)", code)
    return m.group(1) if m else "Main"


class Sandbox:
    name = "none"

    async def execute(self, language: str, code: str, inputs: list[str]) -> ExecResult:
        raise SandboxUnavailable("No sandbox backend is available; code was not executed.")

    async def syntax_check_js(self, code: str) -> ExecResult:
        return await self.execute("javascript-check", code, [])


# ------------------------------------------------------------------ process helpers

async def _run_proc(argv: list[str], stdin: str, cwd: Path, timeout: float, env: dict | None = None,
                    cpu_limit: int | None = None) -> RunOutcome:
    def limits():
        os.setsid()
        if cpu_limit:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(cwd), env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, preexec_fn=limits)
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin.encode()), timeout)
        timed_out = False
    except asyncio.TimeoutError:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = await proc.communicate()
        timed_out = True
    return RunOutcome(stdout=out.decode(errors="replace")[:200_000], stderr=err.decode(errors="replace")[:20_000],
                      exit_code=proc.returncode, timed_out=timed_out, time_s=round(time.monotonic() - t0, 3))


# ------------------------------------------------------------------ Seatbelt (macOS)

SEATBELT_PROFILE = """(version 1)
(deny default)
(allow process-fork)
(allow process-exec)
(allow signal (target same-sandbox))
(allow sysctl-read)
(allow file-read*)
(deny file-read* (subpath "/Users"))
(allow file-read* (subpath "{work}") (subpath "{include}"))
(allow file-write* (subpath "{work}"))
(allow file-write-data (literal "/dev/null") (literal "/dev/dtracehelper"))
(allow file-ioctl (literal "/dev/dtracehelper"))
(allow mach-lookup (global-name "com.apple.system.logger") (global-name "com.apple.system.notification_center")
                   (global-name "com.apple.system.opendirectoryd.libinfo") (global-name "com.apple.coreservices.launchservicesd"))
(allow ipc-posix-shm-read-data)
(deny network*)
"""


class SeatbeltSandbox(Sandbox):
    name = "seatbelt"

    def __init__(self, s: Settings):
        self.s = s
        self.root = Path("/private/tmp/katapilot-sbx")  # outside /Users so the deny rule cannot shadow it
        self.root.mkdir(parents=True, exist_ok=True)
        self.include = self.root / "include"
        (self.include / "bits").mkdir(parents=True, exist_ok=True)
        (self.include / "bits" / "stdc++.h").write_text(BITS_STDCXX)
        self.tools = {t: shutil.which(t) for t in ("clang++", "clang", "python3", "javac", "java", "node")}
        self.sdk_args: list[str] = []
        # /usr/bin/clang and /usr/bin/java are xcrun / java_home shims that misbehave inside the sandbox;
        # resolve the real binaries once, outside it.
        import subprocess

        def _q(*argv: str) -> str | None:
            try:
                out = subprocess.run(argv, capture_output=True, text=True, timeout=20)
                return out.stdout.strip() or None if out.returncode == 0 else None
            except Exception:
                return None

        for t in ("clang++", "clang"):
            real = _q("/usr/bin/xcrun", "-f", t)
            if real:
                self.tools[t] = real
        sdk = _q("/usr/bin/xcrun", "--show-sdk-path")
        if sdk:
            self.sdk_args = ["-isysroot", sdk]
        jhome = _q("/usr/libexec/java_home")
        if jhome and Path(jhome, "bin", "java").exists():
            self.tools["java"], self.tools["javac"] = str(Path(jhome, "bin", "java")), str(Path(jhome, "bin", "javac"))
        elif self.tools.get("java") == "/usr/bin/java":
            self.tools["java"] = self.tools["javac"] = None  # stub without a JDK behind it

    @staticmethod
    def available() -> bool:
        return platform.system() == "Darwin" and Path("/usr/bin/sandbox-exec").exists()

    def _wrap(self, work: Path, argv: list[str]) -> list[str]:
        profile = work / ".sb"
        profile.write_text(SEATBELT_PROFILE.format(work=work.resolve(), include=self.include.resolve()))
        return ["/usr/bin/sandbox-exec", "-f", str(profile), *argv]

    def _env(self, work: Path) -> dict:
        return {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(work), "TMPDIR": str(work),
                "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}

    def _need(self, tool: str) -> str:
        path = self.tools.get(tool)
        if not path:
            raise SandboxUnavailable(f"'{tool}' is not installed, so this language cannot be validated locally.")
        if path.startswith("/Users/"):
            raise SandboxUnavailable(f"'{tool}' lives under /Users ({path}), which the sandbox blocks. Install it system-wide.")
        return path

    async def execute(self, language, code, inputs):
        work = self.root / uuid.uuid4().hex
        work.mkdir()
        env = self._env(work)
        res = ExecResult(backend=self.name)
        try:
            compile_argv: list[str] | None = None
            if language == "cpp":
                (work / "main.cpp").write_text(code)
                compile_argv = [self._need("clang++"), *self.sdk_args, "-std=c++17", "-O2", "-I", str(self.include), "main.cpp", "-o", "main"]
                run_argv = [str(work / "main")]
            elif language == "c":
                (work / "main.c").write_text(code)
                compile_argv = [self._need("clang"), *self.sdk_args, "-std=c11", "-O2", "main.c", "-o", "main", "-lm"]
                run_argv = [str(work / "main")]
            elif language == "java":
                cls = java_class_name(code)
                (work / f"{cls}.java").write_text(code)
                compile_argv = [self._need("javac"), "-J-Xmx512m", f"{cls}.java"]
                run_argv = [self._need("java"), "-Xmx256m", "-Xss64m", "-cp", ".", cls]
            elif language == "python":
                (work / "main.py").write_text(code)
                compile_argv = [self._need("python3"), "-m", "py_compile", "main.py"]
                run_argv = [self._need("python3"), "-I", "main.py"]
            elif language in ("javascript", "javascript-check"):
                (work / "main.js").write_text(code)
                compile_argv = [self._need("node"), "--check", "main.js"]
                run_argv = [self._need("node"), "main.js"]
            else:
                raise SandboxUnavailable(f"Execution for '{language}' is not supported.")

            if compile_argv:
                c = await _run_proc(self._wrap(work, compile_argv), "", work, self.s.sandbox_compile_time_limit_s, env)
                res.compile_output = (c.stdout + c.stderr).replace(str(work) + "/", "")[:8000]
                if c.timed_out or c.exit_code != 0:
                    res.compiled = False
                    return res
            if language == "javascript-check":
                return res
            for stdin in inputs:
                limit = self.s.sandbox_time_limit_s * (3 if language in ("java", "python") else 1)
                res.runs.append(await _run_proc(self._wrap(work, run_argv), stdin, work, limit, env, cpu_limit=int(limit) + 1))
            return res
        finally:
            shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ Docker

DOCKER_IMAGES = {"cpp": "gcc:13", "c": "gcc:13", "java": "eclipse-temurin:17-jdk", "python": "python:3.12-slim",
                 "javascript": "node:20-slim", "javascript-check": "node:20-slim"}


class DockerSandbox(Sandbox):
    name = "docker"

    def __init__(self, s: Settings):
        self.s = s

    @staticmethod
    def available() -> bool:
        return shutil.which("docker") is not None

    async def _docker(self, image: str, work: Path, cmd: str, stdin: str, timeout: float) -> RunOutcome:
        argv = ["docker", "run", "--rm", "-i", "--network", "none", "--memory", f"{self.s.sandbox_memory_mb}m",
                "--cpus", "1", "--pids-limit", "64", "--read-only", "--tmpfs", "/tmp:rw,size=64m",
                "--security-opt", "no-new-privileges", "--cap-drop", "ALL", "-u", "65534:65534",
                "-v", f"{work}:/w:rw", "-w", "/w", image, "sh", "-c", cmd]
        return await _run_proc(argv, stdin, work, timeout + 10)

    async def execute(self, language, code, inputs):
        image = DOCKER_IMAGES.get(language)
        if not image:
            raise SandboxUnavailable(f"Execution for '{language}' is not supported.")
        work = Path(tempfile.mkdtemp(prefix="kp-"))
        work.chmod(0o777)
        res = ExecResult(backend=self.name)
        try:
            if language == "java":
                cls = java_class_name(code)
                (work / f"{cls}.java").write_text(code)
                build, run = f"javac {cls}.java", f"java -Xmx256m -cp . {cls}"
            else:
                fname = LANG_FILES["javascript" if language.startswith("javascript") else language]
                (work / fname).write_text(code)
                build, run = {
                    "cpp": ("g++ -std=c++17 -O2 main.cpp -o main", "./main"),
                    "c": ("gcc -std=c11 -O2 main.c -o main -lm", "./main"),
                    "python": ("python3 -m py_compile main.py", "python3 -I main.py"),
                    "javascript": ("node --check main.js", "node main.js"),
                    "javascript-check": ("node --check main.js", "true"),
                }[language]
            c = await self._docker(image, work, build, "", self.s.sandbox_compile_time_limit_s)
            res.compile_output = (c.stdout + c.stderr)[:8000]
            if c.exit_code != 0 or c.timed_out:
                res.compiled = False
                if "Unable to find image" in c.stderr or "Cannot connect to the Docker daemon" in c.stderr:
                    res.error = "Docker image/daemon unavailable"
                return res
            if language != "javascript-check":
                for stdin in inputs:
                    res.runs.append(await self._docker(image, work, f"timeout {self.s.sandbox_time_limit_s * 2} {run}", stdin,
                                                       self.s.sandbox_time_limit_s * 2))
            return res
        finally:
            shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ Judge0

JUDGE0_LANG = {"cpp": 54, "c": 50, "java": 62, "python": 71, "javascript": 63}


class Judge0Sandbox(Sandbox):
    name = "judge0"

    def __init__(self, s: Settings):
        if not s.judge0_url:
            raise SandboxUnavailable("JUDGE0_URL is not set")
        self.s = s

    async def execute(self, language, code, inputs):
        lid = JUDGE0_LANG.get("javascript" if language == "javascript-check" else language)
        if lid is None:
            raise SandboxUnavailable(f"Execution for '{language}' is not supported.")
        headers = {}
        if self.s.judge0_api_key:
            headers = {"X-RapidAPI-Key": self.s.judge0_api_key, "X-Auth-Token": self.s.judge0_api_key}
        res = ExecResult(backend=self.name)
        async with httpx.AsyncClient(timeout=60) as c:
            for stdin in (inputs or [""]):
                r = await c.post(f"{self.s.judge0_url.rstrip('/')}/submissions?base64_encoded=false&wait=true",
                                 json={"source_code": code, "language_id": lid, "stdin": stdin,
                                       "cpu_time_limit": self.s.sandbox_time_limit_s}, headers=headers)
                if r.status_code >= 400:
                    raise SandboxUnavailable(f"Judge0 error HTTP {r.status_code}")
                d = r.json()
                status = (d.get("status") or {}).get("id")
                if status == 6:
                    res.compiled, res.compile_output = False, d.get("compile_output") or ""
                    return res
                if language == "javascript-check":
                    return res
                res.runs.append(RunOutcome(stdout=d.get("stdout") or "", stderr=(d.get("stderr") or "") + (d.get("message") or ""),
                                           exit_code=0 if status == 3 else 1, timed_out=status == 5,
                                           time_s=float(d.get("time") or 0)))
        return res


def make_sandbox(s: Settings) -> Sandbox:
    choice = s.sandbox_backend
    if choice in ("docker", "auto") and DockerSandbox.available():
        return DockerSandbox(s)
    if choice in ("seatbelt", "auto") and SeatbeltSandbox.available():
        return SeatbeltSandbox(s)
    if choice in ("judge0", "auto") and s.judge0_url:
        return Judge0Sandbox(s)
    return Sandbox()
