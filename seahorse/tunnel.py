"""Public HTTPS address for PayFast's payment notifications while running on your own computer.

PayFast confirms every payment by calling our ITN webhook from its servers, which cannot reach
"localhost". When PAYFAST_TUNNEL=true (and no PUBLIC_SITE_URL / PAYFAST_NOTIFY_URL is set), the site starts a
free Cloudflare "quick tunnel" (via the pycloudflared package — no account needed) and gives PayFast that
address as notify_url. Customers keep using http://127.0.0.1:5000 as normal.
In production you deploy on a real HTTPS domain and set PUBLIC_SITE_URL instead — no tunnel is used.
"""
from __future__ import annotations


def start_tunnel(port: int, log=print) -> str | None:
    try:
        from pycloudflared import try_cloudflare
    except ImportError:
        log("PayFast tunnel: pycloudflared is not installed (python -m pip install pycloudflared).")
        return None
    try:
        urls = try_cloudflare(port=port, verbose=False)
        return urls.tunnel.rstrip("/")
    except Exception as exc:  # noqa: BLE001 — no internet, firewall, unsupported machine …
        log(f"PayFast tunnel could not start ({exc}).")
        return None
