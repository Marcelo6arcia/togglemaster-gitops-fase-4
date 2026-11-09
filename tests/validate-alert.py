#!/usr/bin/env python3
"""Testa o PromQL REAL provisionado no Grafana usando séries sintéticas.

Requer Docker e PyYAML. Não acessa cluster, APM ou destinatários de notificação.
"""

import json
from pathlib import Path
import subprocess
import tempfile

import yaml


ROOT = Path(__file__).resolve().parents[1]
values = yaml.safe_load((ROOT / "observability/values/grafana.yaml").read_text())
rule = values["alerting"]["rules.yaml"]["groups"][0]["rules"][0]
expression = rule["data"][0]["model"]["expr"]
assert rule["title"] == "AuthHigh5xx"
assert rule["labels"]["namespace"] == "togglemaster"
assert rule["labels"]["service"] == "auth-service"

tests = []
for name, success, errors, should_fire in [
    ("sem telemetria nao reinicia", None, None, False),
    ("volume baixo nao reinicia", "0+1x16", "0+1x16", False),
    ("menos de cinco por cento nao reinicia", "0+25x16", "0+1x16", False),
    ("erros sustentados e volume suficiente dispara", "0+20x16", "0+5x16", True),
]:
    series = []
    if success is not None:
        for status, sequence in [("200", success), ("503", errors)]:
            series.append({
                "series": 'app_http_requests_total{service_name="auth-service",'
                'http_route="/validate",http_response_status_code="' + status + '"}',
                "values": sequence,
            })
    tests.append({
        "name": name,
        "interval": "15s",
        "input_series": series,
        "alert_rule_test": [{
            "eval_time": "3m",
            "alertname": rule["title"],
            "exp_alerts": [{"exp_labels": rule["labels"], "exp_annotations": {}}]
            if should_fire else [],
        }],
    })

with tempfile.TemporaryDirectory(prefix="fiap-alert-") as directory:
    tmp = Path(directory)
    # A condição Grafana C é B > 0.5; B reduz a última amostra da consulta A.
    (tmp / "rules.yml").write_text(yaml.safe_dump({"groups": [{
        "name": "grafana-equivalent",
        "interval": "15s",
        "rules": [{"alert": rule["title"], "expr": f"({expression}) > 0.5",
                   "for": rule["for"], "labels": rule["labels"]}],
    }]}))
    (tmp / "test.yml").write_text(yaml.safe_dump({
        "rule_files": ["/config/rules.yml"],
        "evaluation_interval": "15s",
        "tests": tests,
    }))
    subprocess.run([
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--tmpfs", "/tmp", "--entrypoint", "/bin/promtool",
        "-v", f"{tmp}:/config:ro", "prom/prometheus:v3.16.0",
        "test", "rules", "/config/test.yml",
    ], check=True)

dashboard = json.loads((ROOT / "observability/dashboards/togglemaster.json").read_text())
configmap = yaml.safe_load((ROOT / "observability/foundation/dashboard-configmap.yaml").read_text())
assert dashboard == json.loads(configmap["data"]["togglemaster.json"])
print("PASS: quatro cenários do alerta e igualdade entre dashboard fonte e ConfigMap.")
