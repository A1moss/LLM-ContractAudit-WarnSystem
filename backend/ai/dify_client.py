"""
Dify Workflow API Client
调 E 同学的 Dify 工作流：企业合同智能审核系统
"""

import json
import logging
from typing import Any

import requests

from config import DIFY_API_KEY, DIFY_BASE_URL

logger = logging.getLogger(__name__)


def run_workflow(
    inputs: dict[str, Any],
    user: str = "default",
    response_mode: str = "blocking",
) -> dict | None:
    """调用 Dify 工作流，返回执行结果"""
    if not DIFY_API_KEY or DIFY_API_KEY == "app-your-key-here":
        logger.warning("DIFY_API_KEY not configured, skip Dify workflow")
        return None

    url = f"{DIFY_BASE_URL}/v1/workflows/run"
    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": inputs,
        "response_mode": response_mode,
        "user": user,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        result = resp.json()
        logger.info("Dify workflow completed: %s", result.get("workflow_run_id", ""))
        return result
    except requests.exceptions.ConnectionError:
        logger.warning("Dify server not reachable at %s, skip", DIFY_BASE_URL)
        return None
    except Exception as e:
        logger.warning("Dify workflow failed: %s", e)
        return None


def audit_contract(full_text: str) -> list[dict]:
    """调 Dify 工作流审核合同，返回风险列表"""
    result = run_workflow(inputs={"contract_text": full_text})
    if not result:
        return []

    try:
        outputs = result.get("data", {}).get("outputs", {})
        risks = json.loads(outputs.get("risks", "[]"))
        return risks if isinstance(risks, list) else []
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning("Failed to parse Dify output: %s", e)
        return []
