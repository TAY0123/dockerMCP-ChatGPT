"""Network-isolated command and file runner exposed only over a Unix socket."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

WORKSPACE = Path(os.getenv("WORKSPACE", "/workspace")).resolve()
SOCKET_PATH = Path(os.getenv("RUNNER_SOCKET", "/run/runner/runner.sock"))
MAX_COMMAND_LENGTH = int(os.getenv("MAX_COMMAND_LENGTH", "32768"))
MAX_TIMEOUT_SECONDS = int(os.getenv("MAX_TIMEOUT_SECONDS", "300"))
MAX_OUTPUT_BYTES = int(os.getenv("MAX_OUTPUT_BYTES", str(512 * 1024)))
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", str(2 * 1024 * 1024)))
MAX_LIST_ENTRIES = int(os.getenv("MAX_LIST_ENTRIES", "1000"))
MAX_LIST_DEPTH = int(os.getenv("MAX_LIST_DEPTH", "8"))


def _json_error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status_code)


async def _payload(request: Request) -> dict[str, Any]:
    try:
        value = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("Request body must be valid JSON") from None
    if not isinstance(value, dict):
        raise ValueError("Request body must be a JSON object")
    return value


def _workspace_path(value: str, *, allow_root: bool = True) -> Path:
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError("Invalid workspace path")
    candidate = (WORKSPACE / value).resolve(strict=False)
    if candidate != WORKSPACE and WORKSPACE not in candidate.parents:
        raise ValueError("Path escapes the workspace")
    if not allow_root and candidate == WORKSPACE:
        raise ValueError("The workspace root is not allowed for this operation")
    return candidate


def _safe_environment() -> dict[str, str]:
    return {
        "HOME": "/home/sandbox",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/opt/venv/bin:/usr/local/bin:/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "WORKSPACE": str(WORKSPACE),
    }


async def _drain(stream: asyncio.StreamReader | None, limit: int) -> tuple[bytes, bool]:
    if stream is None:
        return b"", False
    output = bytearray()
    truncated = False
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            break
        remaining = limit - len(output)
        if remaining > 0:
            output.extend(chunk[:remaining])
        if len(chunk) > max(remaining, 0):
            truncated = True
    return bytes(output), truncated


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def info(_: Request) -> JSONResponse:
    usage = os.statvfs(WORKSPACE)
    return JSONResponse(
        {
            "ok": True,
            "workspace": str(WORKSPACE),
            "uid": os.getuid(),
            "gid": os.getgid(),
            "network": "disabled",
            "filesystem_root": "read-only",
            "workspace_free_bytes": usage.f_bavail * usage.f_frsize,
            "limits": {
                "max_timeout_seconds": MAX_TIMEOUT_SECONDS,
                "max_output_bytes": MAX_OUTPUT_BYTES,
                "max_file_bytes": MAX_FILE_BYTES,
            },
        }
    )


async def execute(request: Request) -> JSONResponse:
    try:
        data = await _payload(request)
        command = data.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be a non-empty string")
        if len(command) > MAX_COMMAND_LENGTH:
            raise ValueError(f"command exceeds {MAX_COMMAND_LENGTH} characters")

        cwd_value = data.get("cwd", ".")
        if not isinstance(cwd_value, str):
            raise ValueError("cwd must be a string")
        cwd = _workspace_path(cwd_value)
        if not cwd.exists() or not cwd.is_dir():
            raise ValueError("cwd must be an existing directory inside the workspace")

        requested_timeout = data.get("timeout_seconds", 60)
        if not isinstance(requested_timeout, int):
            raise ValueError("timeout_seconds must be an integer")
        timeout_seconds = min(max(requested_timeout, 1), MAX_TIMEOUT_SECONDS)

        process = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-lc",
            command,
            cwd=cwd,
            env=_safe_environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        stdout_task = asyncio.create_task(_drain(process.stdout, MAX_OUTPUT_BYTES))
        stderr_task = asyncio.create_task(_drain(process.stderr, MAX_OUTPUT_BYTES))
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
        except TimeoutError:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()

        stdout, stdout_truncated = await stdout_task
        stderr, stderr_truncated = await stderr_task
        return JSONResponse(
            {
                "ok": process.returncode == 0 and not timed_out,
                "exit_code": process.returncode,
                "timed_out": timed_out,
                "timeout_seconds": timeout_seconds,
                "cwd": str(cwd.relative_to(WORKSPACE)) or ".",
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
            }
        )
    except ValueError as exc:
        return _json_error(str(exc))


async def read_file(request: Request) -> JSONResponse:
    try:
        data = await _payload(request)
        path = _workspace_path(str(data.get("path", "")), allow_root=False)
        requested_limit = data.get("max_bytes", MAX_FILE_BYTES)
        if not isinstance(requested_limit, int):
            raise ValueError("max_bytes must be an integer")
        limit = min(max(requested_limit, 1), MAX_FILE_BYTES)
        if not path.exists() or not path.is_file():
            raise ValueError("path must be an existing file inside the workspace")
        with path.open("rb") as handle:
            content = handle.read(limit + 1)
        truncated = len(content) > limit
        content = content[:limit]
        return JSONResponse(
            {
                "ok": True,
                "path": str(path.relative_to(WORKSPACE)),
                "content": content.decode("utf-8", errors="replace"),
                "truncated": truncated,
                "bytes_returned": len(content),
            }
        )
    except (OSError, ValueError) as exc:
        return _json_error(str(exc))


async def write_file(request: Request) -> JSONResponse:
    try:
        data = await _payload(request)
        raw_path = data.get("path")
        content = data.get("content")
        overwrite = data.get("overwrite", False)
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("path must be a non-empty string")
        if not isinstance(content, str):
            raise ValueError("content must be a string")
        if not isinstance(overwrite, bool):
            raise ValueError("overwrite must be a boolean")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_FILE_BYTES:
            raise ValueError(f"content exceeds {MAX_FILE_BYTES} bytes")

        path = _workspace_path(raw_path, allow_root=False)
        if path.exists() and path.is_symlink():
            raise ValueError("refusing to overwrite a symbolic link")
        if path.exists() and not overwrite:
            raise ValueError("file exists; set overwrite=true to replace it")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded)
        return JSONResponse(
            {"ok": True, "path": str(path.relative_to(WORKSPACE)), "bytes_written": len(encoded)}
        )
    except (OSError, ValueError) as exc:
        return _json_error(str(exc))


async def list_files(request: Request) -> JSONResponse:
    try:
        data = await _payload(request)
        raw_path = data.get("path", ".")
        max_depth = data.get("max_depth", 4)
        max_entries = data.get("max_entries", 500)
        if not isinstance(raw_path, str):
            raise ValueError("path must be a string")
        if not isinstance(max_depth, int) or not isinstance(max_entries, int):
            raise ValueError("max_depth and max_entries must be integers")
        max_depth = min(max(max_depth, 0), MAX_LIST_DEPTH)
        max_entries = min(max(max_entries, 1), MAX_LIST_ENTRIES)
        root = _workspace_path(raw_path)
        if not root.exists() or not root.is_dir():
            raise ValueError("path must be an existing directory inside the workspace")

        entries: list[dict[str, Any]] = []
        truncated = False
        for current_root, dir_names, file_names in os.walk(root, followlinks=False):
            current = Path(current_root)
            depth = len(current.relative_to(root).parts)
            if depth >= max_depth:
                dir_names[:] = []
            dir_names.sort()
            file_names.sort()
            for name in [*dir_names, *file_names]:
                path = current / name
                try:
                    stat = path.lstat()
                except OSError:
                    continue
                if path.is_symlink():
                    kind = "symlink"
                elif path.is_dir():
                    kind = "directory"
                elif path.is_file():
                    kind = "file"
                else:
                    kind = "other"
                entries.append(
                    {
                        "path": str(path.relative_to(WORKSPACE)),
                        "type": kind,
                        "size": stat.st_size,
                    }
                )
                if len(entries) >= max_entries:
                    truncated = True
                    break
            if truncated:
                break

        return JSONResponse({"ok": True, "entries": entries, "truncated": truncated})
    except (OSError, ValueError) as exc:
        return _json_error(str(exc))


app = Starlette(
    routes=[
        Route("/health", health, methods=["GET"]),
        Route("/info", info, methods=["GET"]),
        Route("/execute", execute, methods=["POST"]),
        Route("/read", read_file, methods=["POST"]),
        Route("/write", write_file, methods=["POST"]),
        Route("/list", list_files, methods=["POST"]),
    ]
)


def main() -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET_PATH.exists() or SOCKET_PATH.is_socket():
        SOCKET_PATH.unlink()
    os.umask(0o007)
    uvicorn.run(app, uds=str(SOCKET_PATH), access_log=False, log_level="info")


if __name__ == "__main__":
    main()
