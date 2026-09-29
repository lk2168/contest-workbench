# -*- coding: utf-8 -*-
"""配置：API Key / 模型 / 路径。

读取优先级（先命中先用）：
  1. 环境变量        DEEPSEEK_API_KEY / DEEPSEEK_MODEL / DEEPSEEK_BASE_URL
  2. 仓库根目录 .env （本地文件，不进版本控制）
  3. DSH 的凭据文件   $DSH_HOME/.credentials.yaml 的 refs.DEEPSEEK_API_KEY
     —— 这样你本机不用再拷一份 Key；程序只读取，**绝不打印**。
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "out"

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"  # 本机实测可用：deepseek-flash / deepseek-v4-pro


def _load_dotenv(path: Path) -> dict:
    """极简 .env 解析（KEY=VALUE，# 开头是注释）。"""
    data = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def _load_dsh_key() -> str | None:
    """从 DSH 的凭据文件里取 Key（只读，不打印）。"""
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
        self.api_key = (
            os.environ.get("DEEPSEEK_API_KEY")
            or env_file.get("DEEPSEEK_API_KEY")
            or _load_dsh_key()
            or ""
        )
        self.base_url = (
            os.environ.get("DEEPSEEK_BASE_URL")
            or env_file.get("DEEPSEEK_BASE_URL")
            or DEFAULT_BASE_URL
        ).rstrip("/")
        self.model = (
            os.environ.get("DEEPSEEK_MODEL")
            or env_file.get("DEEPSEEK_MODEL")
            or DEFAULT_MODEL
        )
        # 题库位置也可以写在 .env 里（tools/shiti.py 会读这个环境变量）
        kb = os.environ.get("DIANSAI_KB") or env_file.get("DIANSAI_KB")
        if kb:
            os.environ["DIANSAI_KB"] = kb
        OUT_DIR.mkdir(exist_ok=True)

    @property
    def has_key(self) -> bool:
        return bool(self.api_key.strip())

    def key_source(self) -> str:
        """告诉用户 Key 是从哪来的（不泄露 Key 本身）。"""
        if os.environ.get("DEEPSEEK_API_KEY"):
            return "环境变量 DEEPSEEK_API_KEY"
        if (REPO_ROOT / ".env").exists():
            env = _load_dotenv(REPO_ROOT / ".env")
            if env.get("DEEPSEEK_API_KEY"):
                return ".env 文件"
        if _load_dsh_key():
            return "DSH 凭据文件（~/.dsh/.credentials.yaml）"
        return "未找到"
