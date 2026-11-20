"""Run the browser smoke app with deterministic operator HTML and catalogue HTTP responses."""

import argparse

import httpx
import uvicorn

from app.online import diagnostic_probes, operator_directory


def catalogue(request):
    if request.url.host == "cj.lziapi.com":
        return httpx.Response(410)
    assert request.url.host == "lzzyapi.com"
    return httpx.Response(200, json={"code": 1, "page": 1, "pagecount": 1, "total": 1,
                                     "list": [{"vod_id": 1, "vod_name": "演示节目", "type_name": "电影"}]})


def help_page(snapshot, url, deadline):
    assert url == "https://lzizy.net/index.php/help.html"
    target = "https://lzzyapi.com/api.php/provide/vod/"
    return (f'<div id="tab-home"><div class="api-group"><div class="api-group-title">JSON 采集接口</div>'
            f'<div class="api-row"><span class="api-label">主接口</span><span class="api-box">{target}</span>'
            f'<button class="copy-btn" data-text="{target}">复制</button></div></div></div>').encode()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    diagnostic_probes.client_for = lambda snapshot, hosts: httpx.Client(transport=httpx.MockTransport(catalogue))
    operator_directory._read_page = help_page
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port)
