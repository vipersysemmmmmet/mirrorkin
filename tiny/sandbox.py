"""A throwaway Docker container where tiny you runs code for jobs.

Isolation: no access to the Mac's files (nothing mounted), no Docker socket, all Linux
capabilities dropped, no privilege escalation, capped memory/CPU/processes, and deleted
after each job. It does have internet access, which most coding jobs need.
"""

import os
import shutil
import tarfile
import tempfile
from pathlib import Path
import subprocess
import uuid

IMAGE = "node:22-bookworm"   # Node 22 + Python 3 + git + build tools
WORKDIR = "/home/node/work"
SKILLS_DIR = Path(__file__).resolve().parent.parent / "data" / "skills"   # her growing toolbox
SKILL_LIMIT_BYTES = 2_000_000
SKILL_SUFFIXES = {".py", ".js", ".mjs", ".ts", ".sh", ".md", ".json", ".txt", ".toml", ".yaml", ".yml"}
MAX_OUTPUT = 20000


def docker_bin() -> str:
    found = shutil.which("docker")
    if found:
        return found
    for path in (os.path.expanduser("~/.docker/bin/docker"),
                 "/Applications/Docker.app/Contents/Resources/bin/docker"):
        if os.path.exists(path):
            return path
    return "docker"


class SandboxError(Exception):
    pass


def _docker(*args: str, timeout: int = 120, input_text: str | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([docker_bin(), *args], capture_output=True, text=True,
                              timeout=timeout, input=input_text)
    except FileNotFoundError:
        raise SandboxError("Docker isn't installed.")
    except subprocess.TimeoutExpired:
        raise SandboxError(f"Timed out after {timeout}s.")


def available() -> bool:
    try:
        return _docker("info", "--format", "{{.ServerVersion}}", timeout=15).returncode == 0
    except SandboxError:
        return False


def start() -> str:
    from . import accounts
    name = f"tiny-job-{uuid.uuid4().hex[:8]}"
    env = []
    for key, value in accounts.github_for_sandbox().items():  # her own GitHub, if connected
        env += ["-e", f"{key}={value}"]
    run = _docker("run", "-d", "--name", name, *env,
                  "--memory", "2g", "--cpus", "2", "--pids-limit", "512",
                  "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                  "--user", "node",
                  IMAGE, "sleep", "infinity", timeout=120)
    if run.returncode != 0:
        raise SandboxError(f"Couldn't start the sandbox: {run.stderr.strip()[:300]}")
    # Created by the unprivileged user so it can write there (a --workdir would be root-owned)
    _docker("exec", name, "mkdir", "-p", WORKDIR)
    if SKILLS_DIR.exists() and any(SKILLS_DIR.iterdir()):
        _docker("cp", f"{SKILLS_DIR}/.", f"{name}:{WORKDIR}/skills")
    if env:  # let git push as her, using the token from the environment
        _docker("exec", name, "git", "config", "--global", "credential.helper",
                '!f() { echo username=$GITHUB_USER; echo password=$GH_TOKEN; }; f')
    return name


def stop(name: str) -> None:
    _docker("rm", "-f", name, timeout=60)


def _clip(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT else text[:MAX_OUTPUT // 2] + "\n…[cut]…\n" + text[-MAX_OUTPUT // 2:]


def run(name: str, command: str, timeout: int = 600) -> str:
    result = _docker("exec", "--workdir", WORKDIR, name, "bash", "-lc", command, timeout=timeout)
    return _clip(f"exit code {result.returncode}\n{result.stdout}{result.stderr}")


def write_file(name: str, path: str, content: str) -> str:
    target = path if path.startswith("/") else f"{WORKDIR}/{path}"
    result = _docker("exec", "-i", name, "bash", "-c", 'mkdir -p "$(dirname "$1")" && cat > "$1"', "_", target,
                     input_text=content)
    return f"wrote {target}" if result.returncode == 0 else f"failed: {result.stderr.strip()}"


def read_file(name: str, path: str) -> str:
    target = path if path.startswith("/") else f"{WORKDIR}/{path}"
    result = _docker("exec", name, "cat", target)
    return _clip(result.stdout) if result.returncode == 0 else f"failed: {result.stderr.strip()}"


def remove_leftovers() -> None:
    """Delete job sandboxes left behind by an earlier run of tiny you (only one runs at a time)."""
    try:
        names = _docker("ps", "-aq", "--filter", "name=tiny-job-", timeout=30).stdout.split()
        if names:
            _docker("rm", "-f", *names, timeout=60)
    except SandboxError:
        pass


def save_skills(name: str) -> list[str]:
    """Copy her skills folder back out of the sandbox: plain text files only, size-capped.
    Skills only ever run inside a sandbox, never on the Mac itself."""
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "skills.tar"
        if _docker("exec", name, "test", "-d", f"{WORKDIR}/skills", timeout=30).returncode != 0:
            return []
        dump = subprocess.run([docker_bin(), "exec", name, "tar", "-C", WORKDIR, "-cf", "-", "skills"],
                              capture_output=True, timeout=120).stdout
        if not dump or len(dump) > SKILL_LIMIT_BYTES * 2:
            return []
        archive.write_bytes(dump)
        saved = []
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                path = Path(member.name)
                # only regular text files inside skills/, no links, no escaping the folder
                if (not member.isfile() or path.parts[0] != "skills" or ".." in path.parts
                        or path.suffix not in SKILL_SUFFIXES or member.size > 200_000):
                    continue
                target = SKILLS_DIR / Path(*path.parts[1:])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tar.extractfile(member).read())
                saved.append(str(Path(*path.parts[1:])))
        return saved


def skills_index() -> str:
    """One line per skill: its folder and the first line of its README."""
    if not SKILLS_DIR.exists():
        return ""
    lines = []
    for skill in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir()):
        readme = skill / "README.md"
        summary = readme.read_text().strip().splitlines()[0][:150] if readme.exists() else "(no README)"
        lines.append(f"- skills/{skill.name}: {summary}")
    return "\n".join(lines)
