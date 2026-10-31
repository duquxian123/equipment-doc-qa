"""模型调用的公共部分：读配置、发一次对话请求。

answer.py 和 rewrite.py 都用它，避免两处各写一遍 HTTP。
"""

from __future__ import annotations

from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"


def load_env() -> dict[str, str]:
    if not ENV.exists():
        raise SystemExit(f"缺少 {ENV}，需要配置 DEEPSEEK_API_KEY")
    env = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    if not env.get("DEEPSEEK_API_KEY"):
        raise SystemExit(".env 里没有 DEEPSEEK_API_KEY")
    return env


def chat(system: str, user: str, env: dict[str, str], max_tokens: int = 800) -> str:
    payload = {
        "model": env.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    resp = requests.post(
        f"{env.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com')}/chat/completions",
        headers={"Authorization": f"Bearer {env['DEEPSEEK_API_KEY']}", "Content-Type": "application/json"},
        json=payload,
        timeout=90,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"接口返回 {resp.status_code}：{resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"].strip()
