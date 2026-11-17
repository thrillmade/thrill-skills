"""The census panel goes through Vercel AI Gateway, with one key and one address.

The panel used to authenticate with ANTHROPIC_API_KEY, a secret that died. It
now reads AI_GATEWAY_API_KEY and nothing else, and sends it to a fixed gateway
address with the `anthropic` client. What is pinned here, each on the thing a
reader of the run would see:

- the old secret alone leaves the panel off and the run green (the guard step's
  output, the record the panel writes, and the check `panel_record.sh` makes);
- the client is built with the constant base URL and the gateway key, and the
  request names the gateway's model id;
- the SDK's own environment (base URL, API key, auth token, headers) changes
  neither the host nor the credential;
- the workflow hands the key to the guard step and the panel step and to no
  other step, and names the old secret nowhere;
- a refused budget (HTTP 402, per the gateway's budgets page) is red, with its
  own fixed reason that the shell check also knows.

No test here makes a network request.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PANEL = REPO_ROOT / ".github" / "scripts" / "census_panel.py"
CHECKER = REPO_ROOT / ".github" / "scripts" / "panel_record.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "skill-census.yml"

FAKE_GATEWAY_KEY = "vck_FAKEFIXTUREKEY-0000"
FAKE_OLD_KEY = "sk-ant-api03-FAKEOLDKEY-0000"

_spec = importlib.util.spec_from_file_location("census_panel_gateway_under_test", PANEL)
panel = importlib.util.module_from_spec(_spec)
sys.modules["census_panel_gateway_under_test"] = panel
_spec.loader.exec_module(panel)


def _steps() -> list[dict]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["census"]["steps"]


def _step_script(step_id: str) -> str:
    return next(s for s in _steps() if s.get("id") == step_id)["run"]


# --- a: the old secret alone leaves the panel off ---------------------------


def test_the_old_secret_alone_turns_the_panel_off_and_the_run_stays_green(tmp_path):
    out_file = tmp_path / "github_output"
    env = {
        "PATH": os.environ["PATH"],
        "GITHUB_REPOSITORY": "thrillmade/thrill-skills",
        "GITHUB_OUTPUT": str(out_file),
        "ORCH_APP_ID": "1",
        "ORCH_PRIVATE_KEY": "fake",
        "ANTHROPIC_API_KEY": FAKE_OLD_KEY,
    }
    guard = subprocess.run(["bash", "-c", _step_script("guard")], env=env, capture_output=True, text=True, timeout=60)
    assert guard.returncode == 0, guard.stderr
    assert "panel_enabled=false" in out_file.read_text(encoding="utf-8")
    assert "panel_enabled=true" not in out_file.read_text(encoding="utf-8")

    # The panel step is skipped on that output; run the script anyway, as a
    # local run would, to show the record it leaves and that the check accepts it.
    census = tmp_path / "census"
    census.mkdir()
    (census / "census.json").write_text(json.dumps({"cycle": "census-2026-W47"}), encoding="utf-8")
    run = subprocess.run(
        [sys.executable, str(PANEL)],
        env={"PATH": os.environ["PATH"], "ANTHROPIC_API_KEY": FAKE_OLD_KEY, "CENSUS_DIR": str(census)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode == 0
    check = subprocess.run(["bash", str(CHECKER), str(census), "0"], capture_output=True, text=True, timeout=60)
    assert check.returncode == 0, check.stdout + check.stderr
    record = json.loads((census / "panel_status.json").read_text(encoding="utf-8"))
    assert record["state"] == "off" and record["reason_code"] == "no_key"
    assert FAKE_OLD_KEY not in run.stdout + run.stderr + (census / "panel_status.json").read_text(encoding="utf-8")


def test_the_gateway_secret_turns_the_panel_on(tmp_path):
    out_file = tmp_path / "github_output"
    env = {
        "PATH": os.environ["PATH"],
        "GITHUB_REPOSITORY": "thrillmade/thrill-skills",
        "GITHUB_OUTPUT": str(out_file),
        "ORCH_APP_ID": "1",
        "ORCH_PRIVATE_KEY": "fake",
        "AI_GATEWAY_API_KEY": FAKE_GATEWAY_KEY,
    }
    guard = subprocess.run(["bash", "-c", _step_script("guard")], env=env, capture_output=True, text=True, timeout=60)
    assert guard.returncode == 0, guard.stderr
    assert "panel_enabled=true" in out_file.read_text(encoding="utf-8")


# --- b, c: what the client is built with ------------------------------------


class _Msg:
    content = [type("Block", (), {"text": "{}", "type": "text"})()]
    stop_reason = "end_turn"


class _Recorder:
    """Stands in for the SDK's client class. Records what it was built with, the
    ANTHROPIC_* variables it could have fallen back on, and what was sent."""

    built: list[dict] = []
    sent: list[dict] = []

    def __init__(self, *args, **kwargs):
        type(self).built.append(
            {
                "args": args,
                "kwargs": dict(kwargs),
                "env": {k: v for k, v in os.environ.items() if k.upper().startswith("ANTHROPIC_")},
            }
        )
        self.messages = self

    def create(self, **request):
        type(self).sent.append(request)
        return _Msg()


@pytest.fixture
def recorder(monkeypatch):
    _Recorder.built, _Recorder.sent = [], []
    monkeypatch.setattr(panel, "Anthropic", _Recorder)
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    return _Recorder


def test_the_client_is_built_with_the_constant_address_and_the_gateway_key(recorder, monkeypatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", FAKE_GATEWAY_KEY)
    panel.call_claude("system", "user")
    (built,) = recorder.built
    assert built["args"] == ()
    assert built["kwargs"]["api_key"] == FAKE_GATEWAY_KEY
    assert built["kwargs"]["base_url"] == "https://ai-gateway.vercel.sh"
    assert recorder.sent[0]["model"] == "anthropic/claude-sonnet-5"


def test_the_address_is_a_constant_not_an_input():
    assert panel.GATEWAY_BASE_URL == "https://ai-gateway.vercel.sh"
    source = PANEL.read_text(encoding="utf-8")
    assert "GATEWAY_BASE_URL" in source
    # no environment read, input or file feeds the address
    assert not re.search(r"environ[^\n]*(BASE_URL|GATEWAY_URL)", source)


def test_the_sdks_own_environment_cannot_change_the_host_or_the_credential(recorder, monkeypatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", FAKE_GATEWAY_KEY)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://attacker.example")
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_OLD_KEY)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-FAKE")
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "Authorization: Bearer tok-FAKE")
    panel.call_claude("system", "user")
    (built,) = recorder.built
    # nothing is left for a client to fall back on ...
    assert built["env"] == {}
    # ... and the two values that matter are explicit
    assert built["kwargs"]["base_url"] == "https://ai-gateway.vercel.sh"
    assert built["kwargs"]["api_key"] == FAKE_GATEWAY_KEY
    assert "auth_token" not in built["kwargs"]


def test_the_real_sdk_client_sends_only_the_gateway_key_to_the_gateway(monkeypatch):
    anthropic = pytest.importorskip("anthropic")
    from anthropic._models import FinalRequestOptions
    seen: dict = {}

    class Real(anthropic.Anthropic):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # The request the client would put on the wire: URL and headers.
            options = FinalRequestOptions.construct(method="post", url="/v1/messages", headers={})
            request = self._build_request(options)
            seen["base_url"] = str(request.url)
            seen["headers"] = {k.lower(): v for k, v in request.headers.items()}
            self.messages = _Recorder.__new__(_Recorder)

    _Recorder.sent = []
    monkeypatch.setattr(panel, "Anthropic", Real)
    monkeypatch.setenv("AI_GATEWAY_API_KEY", FAKE_GATEWAY_KEY)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://attacker.example")
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_OLD_KEY)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-FAKE")
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "Authorization: Bearer tok-FAKE")
    panel.call_claude("system", "user")
    assert seen["base_url"].startswith("https://ai-gateway.vercel.sh/")
    # SDK versions differ in which header carries api_key; whichever it is, it
    # is the gateway key, and no other credential header rides along.
    creds = [seen["headers"][h].removeprefix("Bearer ") for h in ("authorization", "x-api-key") if h in seen["headers"]]
    assert creds == [FAKE_GATEWAY_KEY]
    assert "attacker" not in json.dumps(seen)
    assert "tok-FAKE" not in json.dumps(seen) and FAKE_OLD_KEY not in json.dumps(seen)


# --- f: a blank key ---------------------------------------------------------


@pytest.mark.parametrize("blank", [" ", "\n", " \t\n"])
def test_a_blank_gateway_key_is_recorded_as_failed_key_blank(tmp_path, blank):
    census = tmp_path / "census"
    census.mkdir()
    (census / "census.json").write_text(json.dumps({"cycle": "census-2026-W47"}), encoding="utf-8")
    run = subprocess.run(
        [sys.executable, str(PANEL)],
        env={"PATH": os.environ["PATH"], "AI_GATEWAY_API_KEY": blank, "CENSUS_DIR": str(census)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode == 0
    record = json.loads((census / "panel_status.json").read_text(encoding="utf-8"))
    assert record["state"] == "failed" and record["reason_code"] == "key_blank"
    check = subprocess.run(["bash", str(CHECKER), str(census), "0"], capture_output=True, text=True, timeout=60)
    assert check.returncode == 0
    assert "the gateway key is set but blank" in (census / "panel_status.json").read_text(encoding="utf-8")


def test_the_client_refuses_a_blank_key_even_if_called_directly(recorder, monkeypatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "  ")
    with pytest.raises(RuntimeError):
        panel.call_claude("system", "user")
    assert recorder.built == []


# --- d: which steps can see the key -----------------------------------------


def test_the_workflow_names_the_old_secret_nowhere_and_shows_the_key_to_two_steps_only():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" not in text
    assert text.count("secrets.AI_GATEWAY_API_KEY") == 2
    doc = yaml.safe_load(text)
    assert "AI_GATEWAY_API_KEY" not in json.dumps(doc.get("env", {}))
    assert "AI_GATEWAY_API_KEY" not in json.dumps(doc["jobs"]["census"].get("env", {}))
    holders = [s.get("id") for s in _steps() if "secrets.AI_GATEWAY_API_KEY" in json.dumps(s)]
    assert holders == ["guard", "panel"]


# --- 6: a refused budget is red with its own reason -------------------------


class _Status(Exception):
    def __init__(self, status_code):
        self.status_code = status_code


def test_a_budget_refusal_is_red_with_its_own_fixed_reason():
    code, reason, http = panel.classify_failure(_Status(402))
    assert (code, http) == ("budget_exceeded", 402)
    assert code not in panel.TRANSIENT_CODES
    assert "gateway" in reason and reason.endswith("(HTTP 402)")
    text = CHECKER.read_text(encoding="utf-8")
    assert f'budget_exceeded) echo "{reason}"' in text
    assert "budget_exceeded" in re.search(r'^CODES="([^"]*)"', text, re.M).group(1).split()


@pytest.mark.parametrize("status,code", [(401, "authentication"), (403, "permission"), (404, "model_unavailable")])
def test_the_other_failures_that_need_a_person_keep_their_codes(status, code):
    assert panel.classify_failure(_Status(status))[0] == code
    assert code not in panel.TRANSIENT_CODES
