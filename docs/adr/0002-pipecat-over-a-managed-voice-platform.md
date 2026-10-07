# Pipecat over a managed voice platform

We build the voice pipeline ourselves with Pipecat instead of using Vapi or Retell. A managed platform would get a first call working faster, but it hides the parts this project exists to learn: turn detection, where each millisecond of latency goes, and custom processors that sit between STT and the LLM, such as PHI redaction. Owning the pipeline also means we can swap the LLM, STT and TTS freely and trace every stage.
