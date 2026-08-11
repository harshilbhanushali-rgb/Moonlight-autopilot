from pathlib import Path

from app.core.config import settings
from app.llm.factory import build_llm_client
from app.llm.gateway_config import load_llm_gateway_config
from app.prompts.registry import PromptRegistry
from app.services.autofill.repository import SqlAutofillRequestStore

_PROMPTS_ROOT = Path(__file__).resolve().parent.parent / "prompts"
_registry = PromptRegistry(root=_PROMPTS_ROOT)
_gateway_config = load_llm_gateway_config()


def get_llm_client():
    return build_llm_client(settings=settings, gateway_config=_gateway_config)


def get_card_type_prompt():
    return _registry.latest(kind="card_type")


def get_gap_fill_prompt():
    return _registry.latest(kind="gap_fill")


def get_request_store():
    return SqlAutofillRequestStore()


def get_card_table_client():
    raise NotImplementedError(
        "Real external card table client not wired yet — needs Koushik's "
        "card table schema/connection confirmed. Override this dependency "
        "in tests/wiring."
    )
