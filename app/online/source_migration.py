"""Review exact host changes against the saved source and the administrator's draft."""

import hashlib
import json
from urllib.parse import urlsplit


class SourceChanged(RuntimeError):
    """The configuration or proposed changes no longer match the reviewed version."""


def fingerprint(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def preview(source, api_base_url, hosts, expected_fingerprint=None):
    source_version = fingerprint(source)
    if expected_fingerprint is not None and expected_fingerprint != source_version:
        raise SourceChanged()

    old_url = source["api_base_url"]
    old_api, new_api = urlsplit(old_url).hostname, urlsplit(api_base_url).hostname
    changes = []
    if old_api != new_api:
        if old_api in hosts:
            changes.append({"old_host": old_api, "new_host": new_api, "kind": "api", "notice_url": ""})
        if source["provider"] == "maccms":
            from .source_templates import reviewed_host_replacements

            retained = set(source["allowed_hosts"]).intersection(hosts)
            changes.extend(change for change in reviewed_host_replacements(old_url, api_base_url)
                           if change["old_host"] in retained and change["old_host"] != old_api)

    version = fingerprint([source_version, api_base_url, hosts, changes])
    return {"expected_fingerprint": version, "changes": changes}


def hosts_for_update(source, patch, selection):
    """Preserve explicit legacy lists. URL-only patches replace just the old API host."""
    url = patch.get("api_base_url", source["api_base_url"])
    hosts = patch.get("allowed_hosts", source["allowed_hosts"])
    if selection is None:
        old_api, new_api = urlsplit(source["api_base_url"]).hostname, urlsplit(url).hostname
        if "allowed_hosts" in patch or old_api == new_api:
            return hosts
        replacements = {old_api: new_api}
    else:
        report = preview(source, url, hosts)
        if report["expected_fingerprint"] != selection["expected_fingerprint"]:
            raise SourceChanged()
        selected = set(selection["selected_old_hosts"])
        replacements = {change["old_host"]: change["new_host"] for change in report["changes"]
                        if change["old_host"] in selected}
        if selected != replacements.keys():
            raise ValueError("selected host was not proposed")
    return list(dict.fromkeys(replacements.get(host, host) for host in hosts))
