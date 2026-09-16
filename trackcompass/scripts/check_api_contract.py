"""前后端契约一致性检查。

用途：在改动 API 之后，快速确认「前端调用的每个路径，后端都存在」，
以及「后端每个路由，是否有前端在用」——避免出现 404。

用法：
    python scripts/check_api_contract.py [base_url]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8020")
ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / "frontend" / "src" / "api" / "client.ts"

LITERAL = re.compile(r"""url:\s*['"]([^'"]+)['"]""")
TEMPLATE = re.compile(r"url:\s*`([^`]+)`")
OPENAPI_PREFIX = "/api/v1"


def expand(path: str) -> str:
    """把 {id} 与 ${id} 都归一成 X，便于比对。

    注意替换顺序：必须先处理 `${id}`，否则先替换 `{id}` 会残留 `$` 前缀，
    导致 `/a/${id}/b` 与 `/a/{id}/b` 无法匹配（误报 404）。
    """
    path = path.split("?")[0]
    path = re.sub(r"\$\{[^}]+\}", "X", path)
    path = re.sub(r"\{[^}]+\}", "X", path)
    return path


def main() -> int:
    src = CLIENT.read_text(encoding="utf-8")
    frontend_urls = sorted(set(LITERAL.findall(src)) | set(TEMPLATE.findall(src)))

    with httpx.Client(base_url=BASE, timeout=30.0, trust_env=False) as client:
        spec = client.get(f"{OPENAPI_PREFIX}/openapi.json").json()
    backend = {}
    for raw, meta in spec["paths"].items():
        backend[expand(raw.replace(OPENAPI_PREFIX, "", 1))] = sorted(meta.keys())

    print(f"结算：前端调用 {len(frontend_urls)} 个路径，后端注册 {len(backend)} 个路由\n")

    missing = [u for u in frontend_urls if expand(u) not in backend]
    if missing:
        print("❌ 前端调用但后端不存在（会 404）：")
        for u in missing:
            print(f"   - {u}")
    else:
        print("✅ 前端调用的路径全部在后端注册")

    unused = [p for p in sorted(backend) if p not in {expand(u) for u in frontend_urls}]
    if unused:
        print(f"\nℹ️  后端有、前端未用（{len(unused)} 个，供外部集成或后续页面用）：")
        for p in unused:
            print(f"   - [{','.join(backend[p]).upper()}] {OPENAPI_PREFIX}{p}")

    print("\n---- 前端调用清单 ----")
    for u in frontend_urls:
        methods = backend.get(expand(u))
        flag = "✅" if methods else "❌"
        print(f"{flag} {u}  {methods or 'NOT FOUND'}")

    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
