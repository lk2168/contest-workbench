# -*- coding: utf-8 -*-
"""配置：API Key / 模型 / 路径。

Key 的读取优先级（先命中先用）：
  1. 环境变量            DEEPSEEK_API_KEY / DEEPSEEK_MODEL / DEEPSEEK_BASE_URL
  2. 用户配置文件        ~/.contest-workbench/.env      ← 网页端「设置」里填的 Key 存这里
  3. 仓库根目录 .env     （开发时用；不进版本控制）
  4. DSH 的凭据文件      $DSH_HOME/.credentials.yaml 的 refs.DEEPSEEK_API_KEY

★ 为什么要有"用户配置文件"这一层：打包成 exe 后，程序目录可能在 Program Files（**不可写**），
  而且陌生人不会手改 .env。所以网页端填的 Key 必须能落到用户目录，并且优先级要高于仓库 .env
  （用户刚在界面上填的，应该立刻生效）。

路径策略（打包后与开发时不同）：
  - 只读资源（prompts / web 静态文件）→ 打包时随包走，从 bundle 目录读
  - 可写产物（报告 / 上传的数据）      → 优先程序目录旁的 out/，不可写则退回用户目录
  - 题库                                → 优先程序目录旁的 data/题库/（用户自己放）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ── 打包（PyInstaller）与开发环境的差异 ──────────────────────────────────────
FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    # onefile 模式下 __file__ 指向临时解包目录，不能用来定位 exe
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    APP_DIR = Path(sys.executable).resolve().parent      # exe 所在目录（用户可以往这儿放题库）
else:
    BUNDLE_DIR = Path(__file__).resolve().parent.parent
    APP_DIR = BUNDLE_DIR

REPO_ROOT = APP_DIR          # 兼容旧名字：题库、out 都相对它找
BUNDLE_ROOT = BUNDLE_DIR     # 随包资源（prompts / static）

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"  # 本机实测可用：deepseek-flash / deepseek-v4-pro


def env_first(*names: str, default: str = "") -> str:
    """按顺序取第一个非空环境变量。

    用途：**兼容改名前的旧变量名**。本项目 v0.5 从 diansai-agent 改名为 contest-workbench
    （变量前缀 DIANSAI_ → CONTEST_），老用户的脚本/命令行不用改也能继续用。
    """
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default


# 改名前的用户配置目录（v0.5 起用 ~/.contest-workbench）
LEGACY_USER_DIR = Path.home() / ".diansai-agent"


def _migrate_legacy_config(target: Path) -> None:
    """一次性迁移：旧目录里有 Key、新目录里没有 → 复制过来（不覆盖已有文件）。

    没有这一步，改名就等于把用户已经填好的 API Key "弄丢"了。
    """
    try:
        if target.resolve() == LEGACY_USER_DIR.resolve():
            return
        old = LEGACY_USER_DIR / ".env"
        new = target / ".env"
        if old.exists() and not new.exists():
            target.mkdir(parents=True, exist_ok=True)
            new.write_text(old.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass


def user_dir() -> Path:
    """用户级配置目录（可用 CONTEST_CONFIG_DIR 覆盖，测试用）。"""
    env = env_first("CONTEST_CONFIG_DIR", "DIANSAI_CONFIG_DIR")
    d = Path(env) if env else (Path.home() / ".contest-workbench")
    if not env:
        _migrate_legacy_config(d)      # 显式指定目录（测试）时不迁移
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def user_env_file() -> Path:
    return user_dir() / ".env"


def _writable(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write-probe"
        probe.write_text("1", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def _pick_out_dir() -> Path:
    """输出目录：程序目录旁的 out/；不可写（如装在 Program Files）则用用户目录。"""
    cand = REPO_ROOT / "out"
    if _writable(cand):
        return cand
    return user_dir() / "out"


OUT_DIR = _pick_out_dir()


def _load_dotenv(path: Path) -> dict:
    """极简 .env 解析（KEY=VALUE，# 开头是注释）。"""
    data = {}
    if not path.exists():
        return data
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return data
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def save_user_config(api_key: str | None = None, model: str | None = None,
                     base_url: str | None = None) -> Path:
    """把设置写进用户配置文件（保留文件里其它键）。

    只写非 None 的项；传空字符串表示"清空该项"。
    ★ 文件里存的是明文 Key（本地个人机器，与 .env 同级做法）；不打印、不进版本库。
    """
    path = user_env_file()
    data = _load_dotenv(path)
    if api_key is not None:
        data["DEEPSEEK_API_KEY"] = api_key.strip()
    if model is not None:
        data["DEEPSEEK_MODEL"] = model.strip()
    if base_url is not None:
        data["DEEPSEEK_BASE_URL"] = base_url.strip()
    lines = ["# 由「大学生竞赛工作台」网页端设置保存（本机私有，请勿提交到版本库）"]
    for k, v in data.items():
        if v == "":
            continue
        lines.append(f"{k}={v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _load_dsh_key() -> str | None:
    """从 DSH 的凭据文件里取 Key（只读，不打印）。打包后的机器上没有这个文件，属正常。"""
    home = os.environ.get("DSH_HOME") or str(Path.home() / ".dsh")
    p = Path(home) / ".credentials.yaml"
    if not p.exists():
        return None
    try:
        import yaml  # 本机已装 PyYAML；没有就跳过这条路径
    except Exception:
        return None
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return ((d.get("refs") or {}).get("DEEPSEEK_API_KEY")) or None
    except Exception:
        return None


class Config:
    def __init__(self) -> None:
        env_file = _load_dotenv(REPO_ROOT / ".env")
        user_file = _load_dotenv(user_env_file())
        self.api_key = (
            os.environ.get("DEEPSEEK_API_KEY")
            or user_file.get("DEEPSEEK_API_KEY")      # ★ 界面上填的优先于仓库 .env
            or env_file.get("DEEPSEEK_API_KEY")
            or _load_dsh_key()
            or ""
        )
        self.base_url = (
            os.environ.get("DEEPSEEK_BASE_URL")
            or user_file.get("DEEPSEEK_BASE_URL")
            or env_file.get("DEEPSEEK_BASE_URL")
            or DEFAULT_BASE_URL
        ).rstrip("/")
        self.model = (
            os.environ.get("DEEPSEEK_MODEL")
            or user_file.get("DEEPSEEK_MODEL")
            or env_file.get("DEEPSEEK_MODEL")
            or DEFAULT_MODEL
        )
        # 题库位置也可以写在配置文件里（tools/shiti.py 会读这个环境变量）
        # 兼容改名前的 DIANSAI_KB
        kb = (env_first("CONTEST_KB", "DIANSAI_KB")
              or user_file.get("CONTEST_KB") or user_file.get("DIANSAI_KB")
              or env_file.get("CONTEST_KB") or env_file.get("DIANSAI_KB"))
        if kb:
            os.environ["CONTEST_KB"] = kb
        try:
            OUT_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    @property
    def has_key(self) -> bool:
        return bool(self.api_key.strip())

    def masked_key(self) -> str:
        """给界面回显用：只留前 6 位和后 4 位。"""
        k = (self.api_key or "").strip()
        if not k:
            return ""
        if len(k) <= 12:
            return k[:3] + "*" * (len(k) - 3)
        return f"{k[:6]}{'*' * 8}{k[-4:]}"

    def key_source(self) -> str:
        """告诉用户 Key 是从哪来的（不泄露 Key 本身）。"""
        if os.environ.get("DEEPSEEK_API_KEY"):
            return "环境变量 DEEPSEEK_API_KEY"
        if _load_dotenv(user_env_file()).get("DEEPSEEK_API_KEY"):
            return f"用户配置文件（{user_env_file()}）"
        if _load_dotenv(REPO_ROOT / ".env").get("DEEPSEEK_API_KEY"):
            return "仓库 .env 文件"
        if _load_dsh_key():
            return "DSH 凭据文件（~/.dsh/.credentials.yaml）"
        return "未找到"
