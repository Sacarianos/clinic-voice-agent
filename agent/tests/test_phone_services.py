import pytest
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService

from clinic_agent.config import ConfigError
from clinic_agent.conversation import ROLE
from clinic_agent.services import phone_services

KEYS = {"DEEPGRAM_API_KEY": "placeholder", "ANTHROPIC_API_KEY": "placeholder"}


def test_a_phone_call_hears_with_nova_3_thinks_with_haiku_and_speaks_with_aura_2():
    services = phone_services(KEYS)()

    assert isinstance(services.stt, DeepgramSTTService)
    assert services.stt.settings.model == "nova-3-general"
    assert isinstance(services.llm, AnthropicLLMService)
    assert services.llm.settings.model == "claude-haiku-4-5"
    assert services.llm.settings.system_instruction == ROLE
    assert isinstance(services.tts, DeepgramTTSService)
    assert services.tts.settings.voice.startswith("aura-2-")


def test_each_call_gets_its_own_services():
    make_services = phone_services(KEYS)

    first, second = make_services(), make_services()

    assert first.stt is not second.stt
    assert first.llm is not second.llm
    assert first.tts is not second.tts


def test_a_phone_call_reaches_the_ehr_through_the_adapter_on_port_3000_unless_told_otherwise():
    assert phone_services(KEYS)().ehr.base_url == "http://localhost:3000"
    assert phone_services({**KEYS, "EHR_ADAPTER_URL": "http://adapter:3005"})().ehr.base_url == "http://adapter:3005"


@pytest.mark.parametrize("missing", ["DEEPGRAM_API_KEY", "ANTHROPIC_API_KEY"])
def test_server_refuses_to_start_without_a_key_it_needs(missing):
    env = {key: value for key, value in KEYS.items() if key != missing}

    with pytest.raises(ConfigError, match=missing):
        phone_services(env)
