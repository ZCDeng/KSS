"""KSS 托管的 yupi-hot-monitor 运行时（产品化：随装 / 可配 / 可自检 / 常驻）。

布局（STATE_ROOT）::

    yupi/
      repo/                 # git clone 的 yupi-hot-monitor
      logs/server.log
      .kss_yupi_version     # 已安装的 git rev / 标记

默认端口 **18765**（避开本机常见 3001 占用，如 Hermes WebUI）。
环境变量：

- ``KSS_YUPI_PORT``（默认 18765）
- ``KSS_YUPI_REPO_URL``（默认官方 GitHub）
- ``KSS_YUPI_GIT_REF``（默认 master）
- ``OPENROUTER_API_KEY``（yupi AI；可由 Keychain 注入）
- ``KSS_YUPI_MODEL``（写入服务端 env 覆盖，默认 deepseek/deepseek-v3.2）
- ``YUPI_BASE_URL``（客户端；ensure 后默认指向托管实例）
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
_STATE_ROOT = Path(os.environ["KSS_STATE_ROOT"]) if os.environ.get("KSS_STATE_ROOT") else HERE.parents[1]

DEFAULT_PORT = 18765
DEFAULT_REPO = "https://github.com/liyupi/yupi-hot-monitor.git"
DEFAULT_REF = "master"
DEFAULT_MODEL = "deepseek/deepseek-v3.2"


def state_root() -> Path:
    return Path(os.environ["KSS_STATE_ROOT"]) if os.environ.get("KSS_STATE_ROOT") else _STATE_ROOT


def yupi_home() -> Path:
    """安装根：优先 ``KSS_YUPI_HOME``，否则 macOS Application Support/KSS/yupi（与 dev/bundle STATE_ROOT 解耦）。"""
    explicit = (os.environ.get("KSS_YUPI_HOME") or "").strip()
    if explicit:
        return Path(explicit)
    # 产品默认：所有用户同一路径，避免 bridge CLI 与 .app 的 STATE_ROOT 不一致
    mac = Path.home() / "Library" / "Application Support" / "KSS" / "yupi"
    if mac.parent.exists() or os.uname().sysname == "Darwin":
        return mac
    return state_root() / "yupi"


def repo_dir() -> Path:
    return yupi_home() / "repo"


def server_dir() -> Path:
    return repo_dir() / "server"


def log_path() -> Path:
    p = yupi_home() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p / "server.log"


def port() -> int:
    raw = (os.environ.get("KSS_YUPI_PORT") or str(DEFAULT_PORT)).strip()
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_PORT


def base_url() -> str:
    """托管实例 URL；显式 YUPI_BASE_URL 仍优先（调试用）。"""
    explicit = (os.environ.get("YUPI_BASE_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    return f"http://127.0.0.1:{port()}"


def _is_openrouter_base(url: str | None) -> bool:
    u = (url or "").strip().lower()
    if not u:
        return False
    return "openrouter.ai" in u or "openrouter.com" in u or "/openrouter" in u


def resolve_openrouter_key_source() -> tuple[str, str]:
    """返回 (key, source)。source 便于设置页/自检展示。

    优先级：
    1. ``OPENROUTER_API_KEY`` / ``KSS_YUPI_OPENROUTER_KEY``（显式 yupi）
    2. Seesaw 主 LLM 若 base 为 OpenRouter → ``KSS_LLM_PRIMARY_KEY``
    3. Seesaw 备 LLM 若 base 为 OpenRouter → ``KSS_LLM_FALLBACK_KEY``
    4. 旧四键 ``OPENAI_*`` 若 base 为 OpenRouter
    5. 任意候选 key 以 ``sk-or-`` 开头（OpenRouter 形态）
    """
    for k, src in (
        ("OPENROUTER_API_KEY", "openrouter_env"),
        ("KSS_YUPI_OPENROUTER_KEY", "yupi_openrouter_env"),
    ):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v, src

    primary_base = os.environ.get("KSS_LLM_PRIMARY_BASE_URL") or ""
    primary_key = (os.environ.get("KSS_LLM_PRIMARY_KEY") or "").strip()
    if primary_key and _is_openrouter_base(primary_base):
        return primary_key, "seesaw_primary"

    fallback_base = os.environ.get("KSS_LLM_FALLBACK_BASE_URL") or ""
    fallback_key = (os.environ.get("KSS_LLM_FALLBACK_KEY") or "").strip()
    if fallback_key and _is_openrouter_base(fallback_base):
        return fallback_key, "seesaw_fallback"

    openai_base = os.environ.get("OPENAI_BASE_URL") or ""
    openai_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if openai_key and _is_openrouter_base(openai_base):
        return openai_key, "openai_base_openrouter"

    for k, src in (
        ("KSS_LLM_PRIMARY_KEY", "seesaw_primary_sk_or"),
        ("KSS_LLM_FALLBACK_KEY", "seesaw_fallback_sk_or"),
        ("OPENAI_API_KEY", "openai_sk_or"),
    ):
        v = (os.environ.get(k) or "").strip()
        if v.startswith("sk-or-"):
            return v, src
    return "", "none"


def resolve_openrouter_key() -> str:
    key, _ = resolve_openrouter_key_source()
    return key


def resolve_model() -> str:
    """优先 ``KSS_YUPI_MODEL``；否则若 Seesaw 主/备是 OpenRouter 则复用其 model。"""
    explicit = (os.environ.get("KSS_YUPI_MODEL") or "").strip()
    if explicit:
        return explicit
    if _is_openrouter_base(os.environ.get("KSS_LLM_PRIMARY_BASE_URL")):
        m = (os.environ.get("KSS_LLM_PRIMARY_MODEL") or "").strip()
        if m:
            return m
    if _is_openrouter_base(os.environ.get("KSS_LLM_FALLBACK_BASE_URL")):
        m = (os.environ.get("KSS_LLM_FALLBACK_MODEL") or "").strip()
        if m:
            return m
    if _is_openrouter_base(os.environ.get("OPENAI_BASE_URL")):
        m = (os.environ.get("KSS_LLM_MODEL") or "").strip()
        if m:
            return m
    return DEFAULT_MODEL


def _which(cmd: str) -> str | None:
    return shutil.which(cmd)


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = 600,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def node_ok() -> tuple[bool, str]:
    node = _which("node")
    npm = _which("npm")
    if not node or not npm:
        return False, "未找到 node/npm（需 Node.js ≥ 18，建议 brew install node）"
    try:
        proc = _run([node, "-v"], timeout=10)
        ver = (proc.stdout or "").strip()
    except Exception as e:
        return False, f"node 不可用: {e}"
    # parse v18+
    try:
        major = int(ver.lstrip("v").split(".")[0])
    except ValueError:
        major = 0
    if major < 18:
        return False, f"Node {ver} 过旧，需要 ≥ 18"
    return True, f"{ver} @ {node}"


def health(url: str | None = None, timeout: float = 3.0) -> dict[str, Any]:
    target = (url or base_url()).rstrip("/") + "/api/health"
    try:
        req = urllib.request.Request(target, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                data = {"raw": body[:200]}
            return {"ok": True, "url": target, "data": data}
    except Exception as e:
        return {"ok": False, "url": target, "error": str(e)}


def _ensure_repo() -> dict[str, Any]:
    home = yupi_home()
    home.mkdir(parents=True, exist_ok=True)
    repo = repo_dir()
    git = _which("git")
    if not git:
        return {"ok": False, "step": "git", "error": "未找到 git"}
    url = (os.environ.get("KSS_YUPI_REPO_URL") or DEFAULT_REPO).strip()
    ref = (os.environ.get("KSS_YUPI_GIT_REF") or DEFAULT_REF).strip()
    if (repo / ".git").is_dir() and (server_dir() / "package.json").is_file():
        # shallow update best-effort
        _run([git, "-C", str(repo), "fetch", "--depth", "1", "origin", ref], timeout=120)
        _run([git, "-C", str(repo), "checkout", "FETCH_HEAD"], timeout=60)
        return {"ok": True, "step": "repo", "path": str(repo), "action": "updated"}
    if repo.exists():
        shutil.rmtree(repo)
    proc = _run(
        [git, "clone", "--depth", "1", "--branch", ref, url, str(repo)],
        timeout=300,
    )
    if proc.returncode != 0:
        # branch may not work for all remotes — try default clone
        proc = _run([git, "clone", "--depth", "1", url, str(repo)], timeout=300)
    if proc.returncode != 0 or not (server_dir() / "package.json").is_file():
        return {
            "ok": False,
            "step": "clone",
            "error": (proc.stderr or proc.stdout or "clone failed")[:500],
        }
    return {"ok": True, "step": "repo", "path": str(repo), "action": "cloned"}


def _patch_ai_model_env() -> None:
    """让服务端 model 可读 YUPI_AI_MODEL / KSS_YUPI_MODEL（幂等）。"""
    ai = server_dir() / "src" / "services" / "ai.ts"
    if not ai.is_file():
        return
    text = ai.read_text(encoding="utf-8")
    needle = "model: 'deepseek/deepseek-v3.2'"
    repl = "model: process.env.YUPI_AI_MODEL || process.env.KSS_YUPI_MODEL || 'deepseek/deepseek-v3.2'"
    if needle in text and "YUPI_AI_MODEL" not in text:
        ai.write_text(text.replace(needle, repl), encoding="utf-8")


def _patch_tsconfig() -> None:
    """上游 tsconfig 在新 tsc 下 TS5110（module 须 NodeNext）；幂等修正。"""
    tc = server_dir() / "tsconfig.json"
    if not tc.is_file():
        return
    try:
        data = json.loads(tc.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    co = data.setdefault("compilerOptions", {})
    if co.get("moduleResolution") == "NodeNext" and co.get("module") != "NodeNext":
        co["module"] = "NodeNext"
        tc.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _write_env() -> Path:
    env_path = server_dir() / ".env"
    key = resolve_openrouter_key()
    model = resolve_model()
    lines = [
        f'PORT={port()}',
        "CLIENT_URL=http://127.0.0.1:5173",
        f'DATABASE_URL="file:./dev.db"',
        f"OPENROUTER_API_KEY={key}",
        f"YUPI_AI_MODEL={model}",
        f"KSS_YUPI_MODEL={model}",
        "",
    ]
    env_path.write_text("\n".join(lines), encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass
    return env_path


def _npm_install_and_build() -> dict[str, Any]:
    npm = _which("npm")
    node = _which("node")
    if not npm or not node:
        return {"ok": False, "error": "npm/node missing"}
    srv = server_dir()
    env = os.environ.copy()
    # install
    proc = _run([npm, "install", "--no-fund", "--no-audit"], cwd=srv, timeout=600)
    if proc.returncode != 0:
        return {"ok": False, "step": "npm_install", "error": (proc.stderr or proc.stdout)[:800]}
    # prisma
    proc = _run([npm, "run", "db:generate"], cwd=srv, timeout=180)
    if proc.returncode != 0:
        # try npx
        proc = _run([npm, "exec", "--", "prisma", "generate"], cwd=srv, timeout=180)
    if proc.returncode != 0:
        return {"ok": False, "step": "prisma_generate", "error": (proc.stderr or proc.stdout)[:800]}
    proc = _run([npm, "run", "db:push"], cwd=srv, timeout=180)
    if proc.returncode != 0:
        proc = _run([npm, "exec", "--", "prisma", "db", "push"], cwd=srv, timeout=180)
    if proc.returncode != 0:
        return {"ok": False, "step": "prisma_push", "error": (proc.stderr or proc.stdout)[:800]}
    # build
    proc = _run([npm, "run", "build"], cwd=srv, timeout=300)
    if proc.returncode != 0:
        return {"ok": False, "step": "build", "error": (proc.stderr or proc.stdout)[:800]}
    dist = srv / "dist" / "index.js"
    if not dist.is_file():
        return {"ok": False, "step": "build", "error": "dist/index.js missing after build"}
    return {"ok": True, "dist": str(dist)}


def install(*, force_reinstall: bool = False) -> dict[str, Any]:
    """安装/更新 yupi 到 STATE_ROOT（不启动）。"""
    ok_node, node_detail = node_ok()
    if not ok_node:
        return {"ok": False, "error": node_detail, "node": node_detail}

    steps: list[dict[str, Any]] = [{"step": "node", "ok": True, "detail": node_detail}]
    if force_reinstall and repo_dir().exists():
        shutil.rmtree(repo_dir())

    r = _ensure_repo()
    steps.append(r)
    if not r.get("ok"):
        return {"ok": False, "steps": steps, "error": r.get("error")}

    _patch_ai_model_env()
    _patch_tsconfig()
    env_path = _write_env()
    steps.append({"step": "env", "ok": True, "path": str(env_path), "has_openrouter_key": bool(resolve_openrouter_key())})

    b = _npm_install_and_build()
    steps.append({"step": "npm_build", **b})
    if not b.get("ok"):
        # 回退：tsx 直接跑 src（不依赖 tsc 产物）
        npm = _which("npm")
        if npm:
            proc = _run([npm, "install", "--no-fund", "--no-audit", "tsx"], cwd=server_dir(), timeout=300)
            steps.append({
                "step": "tsx_fallback",
                "ok": proc.returncode == 0,
                "detail": (proc.stderr or proc.stdout or "")[:300],
            })
            if proc.returncode == 0 and (server_dir() / "src" / "index.ts").is_file():
                b = {"ok": True, "dist": str(server_dir() / "src" / "index.ts"), "runner": "tsx"}
            else:
                return {"ok": False, "steps": steps, "error": b.get("error")}
        else:
            return {"ok": False, "steps": steps, "error": b.get("error")}

    (yupi_home() / ".kss_yupi_version").write_text(
        f"port={port()}\nmodel={resolve_model()}\nbase_url={base_url()}\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "steps": steps,
        "port": port(),
        "base_url": base_url(),
        "model": resolve_model(),
        "repo": str(repo_dir()),
        "has_openrouter_key": bool(resolve_openrouter_key()),
    }


def _server_entry() -> tuple[list[str], str] | None:
    """返回 (argv, kind)；优先 dist，否则 npx tsx src。"""
    dist = server_dir() / "dist" / "index.js"
    node = _which("node")
    if not node:
        return None
    if dist.is_file():
        return [node, str(dist)], "dist"
    src = server_dir() / "src" / "index.ts"
    npx = _which("npx")
    if src.is_file() and npx:
        return [npx, "--yes", "tsx", str(src)], "tsx"
    return None


def start_background(*, allow_install: bool = False) -> dict[str, Any]:
    """若 health 未通则后台启动 yupi server。

    ``allow_install=False``（默认）：仅启动已构建实例，避免热路径阻塞 npm。
    ``allow_install=True``：缺 entry 时先 install（给 yupi-ensure / 自检用）。
    """
    h = health()
    if h.get("ok"):
        return {"ok": True, "already_running": True, "health": h, "base_url": base_url()}

    entry = _server_entry()
    if entry is None:
        if not allow_install:
            return {"ok": False, "error": "yupi not installed; run yupi-ensure"}
        inst = install()
        if not inst.get("ok"):
            return {"ok": False, "error": "install failed", "install": inst}
        entry = _server_entry()
    if entry is None:
        return {"ok": False, "error": "no server entry (dist/tsx)"}

    argv, kind = entry
    _write_env()  # refresh keys

    log = log_path()
    env = os.environ.copy()
    env["PORT"] = str(port())
    env["YUPI_AI_MODEL"] = resolve_model()
    env["KSS_YUPI_MODEL"] = resolve_model()
    key = resolve_openrouter_key()
    if key:
        env["OPENROUTER_API_KEY"] = key

    # detach
    logf = open(log, "a", encoding="utf-8")  # noqa: SIM115 — kept open for subprocess
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(server_dir()),
            env=env,
            stdout=logf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as e:
        logf.close()
        return {"ok": False, "error": str(e)}

    # wait health
    for _ in range(40):
        time.sleep(0.5)
        h = health()
        if h.get("ok"):
            return {
                "ok": True,
                "already_running": False,
                "pid": proc.pid,
                "runner": kind,
                "health": h,
                "base_url": base_url(),
                "log": str(log),
            }
    return {
        "ok": False,
        "error": "started but health not ready",
        "pid": proc.pid,
        "runner": kind,
        "log": str(log),
        "health": health(),
    }


def ensure(*, start: bool = True, force_reinstall: bool = False) -> dict[str, Any]:
    """产品入口：安装（如需）+ 可选启动 + 返回状态。"""
    h0 = health()
    if h0.get("ok") and not force_reinstall:
        return {
            "ok": True,
            "base_url": base_url(),
            "port": port(),
            "model": resolve_model(),
            "health": h0,
            "has_openrouter_key": bool(resolve_openrouter_key()),
            "action": "already_healthy",
        }

    need_install = force_reinstall or _server_entry() is None
    install_result = None
    if need_install:
        install_result = install(force_reinstall=force_reinstall)
        if not install_result.get("ok"):
            return {
                "ok": False,
                "error": install_result.get("error"),
                "install": install_result,
                "base_url": base_url(),
                "has_openrouter_key": bool(resolve_openrouter_key()),
            }

    if not start:
        return {
            "ok": True,
            "base_url": base_url(),
            "port": port(),
            "model": resolve_model(),
            "install": install_result,
            "action": "installed_only",
            "has_openrouter_key": bool(resolve_openrouter_key()),
        }

    started = start_background(allow_install=True)
    return {
        "ok": bool(started.get("ok")),
        "base_url": base_url(),
        "port": port(),
        "model": resolve_model(),
        "install": install_result,
        "start": started,
        "health": started.get("health") or health(),
        "has_openrouter_key": bool(resolve_openrouter_key()),
        "action": "ensured",
        "error": started.get("error"),
    }


def status() -> dict[str, Any]:
    h = health()
    installed = (server_dir() / "dist" / "index.js").is_file() or (
        server_dir() / "src" / "index.ts"
    ).is_file()
    key, key_source = resolve_openrouter_key_source()
    return {
        "base_url": base_url(),
        "port": port(),
        "model": resolve_model(),
        "installed": installed,
        "repo": str(repo_dir()) if repo_dir().exists() else None,
        "health_ok": bool(h.get("ok")),
        "health": h,
        "has_openrouter_key": bool(key),
        "openrouter_key_source": key_source,
        "node": node_ok()[1],
        "node_ok": node_ok()[0],
    }
