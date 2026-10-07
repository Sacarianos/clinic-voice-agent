"""The outside services one phone call uses: Deepgram for speech, the configured LLM for replies, the EHR adapter."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pipecat.processors.frame_processor import FrameProcessor
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.llm_service import LLMService
from pipecat.transcriptions.language import Language

from clinic_agent.clinic import PROVIDER_NAMES
from clinic_agent.config import require
from clinic_agent.conversation import ROLE
from clinic_agent.ehr import DEFAULT_EHR_ADAPTER_URL, EhrAdapter
from clinic_agent.llm import create_llm


@dataclass(frozen=True)
class VoiceServices:
    stt: FrameProcessor
    llm: LLMService
    tts: FrameProcessor
    ehr: EhrAdapter


def phone_services(env: Mapping[str, str]) -> Callable[[], VoiceServices]:
    """Check the keys now so a missing one stops the server at startup, not on the first call."""
    deepgram_key = require(env, "DEEPGRAM_API_KEY", "Deepgram speech-to-text and text-to-speech")
    create_llm(env, system_instruction=ROLE)
    ehr_adapter_url = env.get("EHR_ADAPTER_URL") or DEFAULT_EHR_ADAPTER_URL

    def make_services() -> VoiceServices:
        return VoiceServices(
            stt=DeepgramSTTService(
                api_key=deepgram_key,
                mip_opt_out=True,  # keep call audio out of Deepgram's model-improvement program
                settings=DeepgramSTTService.Settings(
                    model="nova-3-general", language=Language.EN, keyterm=_provider_keyterms()
                ),
            ),
            llm=create_llm(env, system_instruction=ROLE),
            tts=DeepgramTTSService(
                api_key=deepgram_key,
                settings=DeepgramTTSService.Settings(voice="aura-2-helena-en"),
            ),
            ehr=EhrAdapter(ehr_adapter_url),
        )

    return make_services


def _provider_keyterms() -> list[str]:
    """Each Provider's full name and surname, so Nova-3 hears "Dr. Szczepanski" as well as the full name."""
    return [*PROVIDER_NAMES, *(name.split()[-1] for name in PROVIDER_NAMES)]
