"""The Read-back, and the write that only a recorded yes to it can reach. Book, Reschedule and Cancel all use it.

Per ADR 0003 the details and the yes are both enforced here. The agent itself speaks the details,
and the Read-back node offers one tool: record_read_back_answer, which records the Caller's answer as
yes, no or change. The LLM still judges what the Caller meant, but as a separate step. Only a recorded
yes moves on, to a node whose only tool is the write for exactly the details read back. No and change
go back to choosing. So a stray write call at the Read-back is refused, whatever the Caller said.
"""

from collections.abc import Callable

from pipecat.flows import FlowManager, FlowsFunctionSchema, NodeConfig

from clinic_agent.escalation import unless_the_call_is_ending

ANSWERS = ("yes", "no", "change")

READ_BACK_TASK = """\
You just read back what will be done and asked the caller if that is right.
Here the only tool for it is record_read_back_answer. Call it with the caller's answer:
- yes when they clearly agree to exactly what you read back
- no when they say it is wrong
- change when they want something different, or ask about other times, Providers, visit types or
  appointments. That takes you back to where you can search and choose again, so call it first.
If they are unsure or ask something else, answer and ask again without calling it.
Never say it is done: nothing has been written yet.
"""

WRITE_TASK = """\
The caller said yes to the Read-back. Call {tool} now, without saying anything first.
Never say it is done before {tool} returns.
"""


def read_back_node(
    name: str, line: str, *, then_write: NodeConfig, choose_again: Callable[[], NodeConfig]
) -> NodeConfig:
    """Speaks the line, which must hold every detail the write acts on, then waits for the Caller's answer.

    then_write is where a yes goes: a write_node for exactly these details. choose_again builds the
    node a no or a change goes back to.
    """

    async def record_read_back_answer(args: dict, flow_manager: FlowManager):
        answer = args.get("answer")
        if answer == "yes":
            return {"answer": answer}, then_write
        if answer in ANSWERS:
            return {"answer": answer}, choose_again()
        return {"status": "error", "error": f"answer must be one of {', '.join(ANSWERS)}"}, None

    answer_tool = FlowsFunctionSchema(
        name="record_read_back_answer",
        description=(
            "Record the caller's answer to the Read-back you just gave. "
            "Use change before looking at other times, Providers or appointments."
        ),
        properties={"answer": {"type": "string", "enum": list(ANSWERS)}},
        required=["answer"],
        handler=unless_the_call_is_ending(record_read_back_answer),
        cancel_on_interruption=True,
    )
    return {
        "name": name,
        "pre_actions": [{"type": "tts_say", "text": line}],
        "task_messages": [{"role": "developer", "content": READ_BACK_TASK}],
        "functions": [answer_tool],
        "respond_immediately": False,
    }


def write_node(name: str, write_tool: FlowsFunctionSchema) -> NodeConfig:
    """The node after a recorded yes. Its only tool is the write, which takes no arguments."""
    return {
        "name": name,
        "task_messages": [{"role": "developer", "content": WRITE_TASK.format(tool=write_tool.name)}],
        "functions": [write_tool],
    }
