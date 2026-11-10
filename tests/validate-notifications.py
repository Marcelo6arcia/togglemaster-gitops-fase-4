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
contact = alerting["contactpoints.yaml"]["contactPoints"][0]
receivers = {item["type"]: item for item in contact["receivers"]}
assert set(receivers) == {"pagerduty", "discord", "webhook"}
assert all(item["disableResolveMessage"] is False for item in receivers.values())

pagerduty = receivers["pagerduty"]["settings"]
discord = receivers["discord"]["settings"]
webhook = receivers["webhook"]["settings"]
assert pagerduty["integrationKey"] == "$PAGERDUTY_INTEGRATION_KEY"
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
assert discord["title"] == pagerduty["summary"] == '{{ template "fiap.incident.title" . }}'
assert discord["message"] == pagerduty["details"]["contexto"] == '{{ template "fiap.incident.message" . }}'
for field in (".Status", ".Labels.service", ".Labels.namespace", ".Annotations.summary",
              ".Annotations.description", ".StartsAt", ".EndsAt", ".Annotations.runbook_url"):
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
expected = {"RESPONDER_TOKEN", "PAGERDUTY_INTEGRATION_KEY", "DISCORD_WEBHOOK_URL"}
assert {item["secretKey"] for item in incident["spec"]["data"]} == expected
for item in incident["spec"]["data"]:
    assert item["remoteRef"] == {"key": "togglemaster/dev/observability/incidents", "property": item["secretKey"]}
deployment = next(item for item in objects if item["kind"] == "Deployment")
assert deployment["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/name"] == "grafana"
container = next(item for item in deployment["spec"]["template"]["spec"]["containers"] if item["name"] == "grafana")
assert {"secretRef": {"name": "incident-integrations"}} in container["envFrom"]
print("PASS: contatos, resolucao, agrupamento, templates renderizados e referencias de Secrets.")
