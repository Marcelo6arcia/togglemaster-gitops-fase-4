#!/usr/bin/env python3
"""Confere os contratos de notificacao no YAML renderizado pelo Helm, via stdin.

Nao resolve variaveis, acessa segredos nem envia notificacoes.
"""

from pathlib import Path
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
objects = [item for item in yaml.safe_load_all(sys.stdin.read()) if item]
config = {key: value for item in objects if item["kind"] == "ConfigMap"
          for key, value in item.get("data", {}).items()}
alerting = {name: yaml.safe_load(config[name]) for name in (
    "contactpoints.yaml", "templates.yaml", "policies.yaml", "rules.yaml",
)}
contacts = alerting["contactpoints.yaml"]["contactPoints"]
assert len(contacts) == 1
contact = contacts[0]
assert contact["name"] == "togglemaster-incident" and contact["orgId"] == 1
receivers = {item["uid"]: item for item in contact["receivers"]}
expected_types = {
    "fiap-jsm": "opsgenie", "fiap-jira-automation": "webhook",
    "fiap-discord": "discord", "fiap-responder": "webhook",
}
assert len(contact["receivers"]) == len(expected_types)
assert {uid: item["type"] for uid, item in receivers.items()} == expected_types
assert all(item["disableResolveMessage"] is False for item in receivers.values())

incident_settings = receivers["fiap-jsm"]["settings"]
discord = receivers["fiap-discord"]["settings"]
webhook = receivers["fiap-responder"]["settings"]
assert incident_settings == {
    "apiKey": "$JSM_OPERATIONS_API_KEY",
    "apiUrl": "$JSM_OPERATIONS_API_URL",
    "message": '{{ template "fiap.incident.title" . }}',
    "description": '{{ template "fiap.incident.message" . }}',
    "autoClose": True,
    "sendTagsAs": "both",
}
jira_automation = receivers["fiap-jira-automation"]["settings"]
# Jira Automation recebe o corpo Grafana padrao; autenticacao pelo header exato,
# sem Bearer e sem payload customizado. Headers nao sao campos Secure no Grafana.
assert jira_automation == {
    "url": "$JIRA_AUTOMATION_WEBHOOK_URL",
    "httpMethod": "POST",
    "headers": {"X-Automation-Webhook-Token": "$JIRA_AUTOMATION_WEBHOOK_TOKEN"},
    "maxAlerts": "10",
}
base = yaml.safe_load((ROOT / "observability/values/grafana.yaml").read_text())
candidate = yaml.safe_load((ROOT / "observability/values/grafana-jsm.yaml").read_text())
assert set(candidate) == {"alerting"}
assert set(candidate["alerting"]) == {"contactpoints.yaml"}
base_contact = base["alerting"]["contactpoints.yaml"]["contactPoints"][0]
candidate_contact = candidate["alerting"]["contactpoints.yaml"]["contactPoints"][0]
# A lista e substituida pelo Helm: Discord e responder precisam continuar identicos.
for uid in ("fiap-discord", "fiap-responder"):
    base_receiver = next(item for item in base_contact["receivers"] if item["uid"] == uid)
    candidate_receiver = next(item for item in candidate_contact["receivers"] if item["uid"] == uid)
    assert candidate_receiver == base_receiver, f"Override mudou receiver comum: {uid}"
assert discord["url"] == "$DISCORD_WEBHOOK_URL"
assert webhook["url"] == "http://incident-responder.observability.svc.cluster.local:8080/webhook"
assert webhook["authorization_scheme"] == "Bearer"
assert webhook["authorization_credentials"] == "$RESPONDER_TOKEN"
assert webhook["httpMethod"] == "POST"
# O responder consome o payload padrao Grafana, nao um template customizado.
assert not any(key.startswith("payload") for key in webhook)
assert int(webhook["maxAlerts"]) == 10

templates = alerting["templates.yaml"]["templates"]
assert len(templates) == 1 and templates[0]["orgId"] == contact["orgId"]
definition = templates[0]["template"]
assert '{{`' not in definition, "Helm nao removeu os escapes dos templates Grafana"
for name in ("fiap.incident.title", "fiap.incident.message"):
    assert '{{ define "' + name + '"' in definition
assert discord["title"] == '{{ template "fiap.incident.title" . }}'
assert discord["message"] == '{{ template "fiap.incident.message" . }}'
for field in (".Status", ".Labels.service", ".Labels.namespace", ".Annotations.summary",
              ".Annotations.description", ".StartsAt", ".EndsAt", ".Fingerprint", ".Annotations.runbook_url"):
    assert field in definition, f"Mensagem sem contexto necessario: {field}"

policy = alerting["policies.yaml"]["policies"][0]
assert policy["receiver"] == contact["name"]
assert set(policy["group_by"]) == {"alertname", "namespace", "service"}
assert policy["group_wait"] == "0s", "A primeira notificacao nao deve aguardar agrupamento"
assert policy["group_interval"] == "30s"
rule = alerting["rules.yaml"]["groups"][0]["rules"][0]
assert rule["title"] == "AuthHigh5xx"
assert rule["labels"]["service"] == "auth-service"
assert rule["labels"]["namespace"] == "togglemaster"

externals = list(yaml.safe_load_all((ROOT / "observability/foundation/externalsecrets.yaml").read_text()))
incident = next(item for item in externals if item["metadata"]["name"] == "incident-integrations")
assert incident["metadata"]["namespace"] == "observability"
assert incident["spec"]["target"]["name"] == "incident-integrations"
expected = {
    "RESPONDER_TOKEN", "DISCORD_WEBHOOK_URL", "JSM_OPERATIONS_API_KEY",
    "JSM_OPERATIONS_API_URL", "JIRA_AUTOMATION_WEBHOOK_URL", "JIRA_AUTOMATION_WEBHOOK_TOKEN",
}
secret_keys = {item["secretKey"] for item in incident["spec"]["data"]}
assert secret_keys == expected, "ExternalSecret nao corresponde aos receivers ativos"
for item in incident["spec"]["data"]:
    assert item["remoteRef"] == {"key": "togglemaster/dev/observability/incidents", "property": item["secretKey"]}
deployment = next(item for item in objects if item["kind"] == "Deployment")
assert deployment["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/name"] == "grafana"
container = next(item for item in deployment["spec"]["template"]["spec"]["containers"] if item["name"] == "grafana")
assert {"secretRef": {"name": "incident-integrations"}} in container["envFrom"]
print("PASS: quatro receivers ativos, templates, resolucao, agrupamento e ExternalSecret JSM.")
