import xml.etree.ElementTree as ET

from starlette.testclient import TestClient

from clinic_agent.server import create_app


def _no_services():
    raise AssertionError("the webhook must not build a pipeline")


def test_webhook_answers_with_twiml_that_streams_the_call_to_the_websocket():
    client = TestClient(create_app(_no_services))

    response = client.post(
        "/voice",
        data={"CallSid": "CA00000000000000000000000000000001", "From": "+15555550123", "To": "+15555550100"},
        headers={"host": "clinic-agent.ngrok-free.app"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    twiml = ET.fromstring(response.text)
    assert twiml.tag == "Response"
    [connect] = twiml
    assert connect.tag == "Connect"
    [stream] = connect
    assert stream.tag == "Stream"
    assert stream.get("url") == "wss://clinic-agent.ngrok-free.app/ws"
