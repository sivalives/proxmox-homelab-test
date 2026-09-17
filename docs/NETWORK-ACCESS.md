# Network Access — Tailscale, Nginx, and DNS

How requests reach services like Splunk and Pi-hole from anywhere, and why each piece exists. This covers three jobs that are easy to conflate but solve completely different problems.

## The three jobs

### Job 1: Tailscale — "How do I even reach my home network from anywhere?"

Gets a remote device (phone on mobile data, laptop at a coffee shop) into the home LAN through a secure tunnel. Nothing more.

### Job 2: Nginx — "I'm inside the house, now route me to the right room."

Sits at one address inside the LAN and forwards each request to the correct backend container based on which hostname was requested.

### Job 3: DNS (Pi-hole or `/etc/hosts`) — "Translate the name into an address."

Browsers don't understand `splunk.lan` — they need an IP. Something has to answer that lookup before the browser ever talks to Nginx.

## Request flow diagram

![Tailscale, Nginx, DNS request flow](./diagrams/tailscale-nginx-dns-flow.svg)

A laptop asks for `splunk.lan`. DNS resolves the name to `192.168.1.106`.

If the laptop is remote, Tailscale's subnet route tunnels it onto the home LAN; if it's already on home Wi-Fi, this step is skipped. Either way, the request lands on Nginx at `192.168.1.106`, which reads the requested hostname and proxies to the Splunk container.

## Why all three are needed

| Only this                   | What breaks                                                                                                    |
| --------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Tailscale, no DNS, no Nginx | Works, but you must remember `http://192.168.1.106:8000` style IP:port for every service. Ugly but functional. |
| DNS, no Nginx               | Each service needs its own dedicated IP — wasteful, doesn't scale as services grow.                            |
| Nginx, no DNS               | Browsers can't reach Nginx by name at all; only `curl -H "Host: ..."` tricks work, not real browsing.          |
| All three                   | Clean name → one shared IP (Nginx) → correct container → reachable from anywhere via Tailscale.                |

## Setup summary

### Tailscale (Pi-hole LXC)

Tailscale is installed on the Pi-hole LXC (`192.168.1.104`) — not on every LXC.

The Pi-hole LXC advertises the home subnet so any Tailscale-connected device can reach LXCs by their normal LAN IP, with no per-LXC Tailscale installation needed.

The subnet route is:

```bash
192.168.1.0/24
```

The advertised route must be approved once in the Tailscale admin console.

Once approved, any Tailscale device can reach services using their **real LAN IP** — for example:

```text
http://192.168.1.102:8000
```

for Splunk — exactly as if standing on the home network. There is no separate "Tailscale IP" to remember for each service.

### Nginx (LXC 100)

One reverse proxy, one address (`192.168.1.106`), routing by the `Host` header to whichever container the request named:

```text
splunk.lan  → 192.168.1.102:8000  (Splunk, different LXC)

pihole.lan  → 192.168.1.104:80    (Pi-hole, different LXC)

dockge.lan  → 192.168.1.106:5001  (Dockge, same LXC)
```

**Networking mode — important.**

Nginx runs with `network_mode: host`, not the default Docker bridge. Bridge networking NATs the connection before it reaches the container, so Nginx only ever sees Docker's internal gateway IP (`172.x.x.x`) as the client — every `allow`/`deny` rule then matches the wrong address and legitimate requests get a `403`.

Host networking removes that NAT layer so Nginx sees the real client IP (Tailscale CGNAT range or home LAN), and `allow`/`deny` works correctly.

```nginx
allow 100.64.0.0/10;    # Tailscale CGNAT range — covers every Tailscale device
allow 192.168.1.0/24;   # home LAN

deny all;
```

`100.64.0.0/10` is not one device's IP — it's the entire range Tailscale assigns devices from. Hardcoding a single Tailscale IP would break the moment that device reconnects and gets a new one; the range covers laptop, phone, and any future device automatically.

**Tradeoff of host networking:** only Nginx uses it. Every other container in LXC 100 (Mosquitto, MongoDB, InfluxDB, Vault, etc.) stays on normal bridge networking and maps its own port.

Since Nginx shares the host's network stack, it reaches those containers at `127.0.0.1:<port>` with zero conflict.

Ports `80`/`443` belong exclusively to Nginx on this LXC — no other container can also bind those, but every other port remains free to use normally.

### DNS — when each method applies

This is the piece that's easiest to get backwards, so it's worth being explicit about exactly when each one is doing the work.

### Pi-hole local DNS records

Used when a **device's actual DNS server** is Pi-hole (`192.168.1.104`).

In this homelab, that's the case for **any device on home Wi-Fi**, because the Orbi router's DNS setting was pointed at Pi-hole.

That's the entire reason `splunk.lan` works on a phone with zero configuration on the phone itself — the phone never holds the answer; it asks the router, the router asks Pi-hole, and Pi-hole answers.

```text
Phone on home Wi-Fi
        ↓
asks Orbi for DNS
        ↓
Orbi forwards to Pi-hole
        ↓
Pi-hole answers 192.168.1.106
```

Records are managed in Pi-hole's web UI under **Settings → Local DNS Records** (Pi-hole v6+; the legacy `dnsmasq.d/custom.conf` file approach does not apply to v6).

Consequence: **if the Pi-hole container or LXC is down, every device using the router's DNS loses `.lan` resolution** — not just for Splunk, but for any name that only Pi-hole knows.

General internet browsing for non-`.lan` sites is unaffected only if a secondary DNS (e.g. `1.1.1.1`) is also configured as a fallback on the router.

A subtlety worth remembering: phones cache DNS answers. Stopping Pi-hole does not break `.lan` names immediately on a device that already resolved them recently — it appears to "still work" until the cache expires or the device reconnects to Wi-Fi, at which point the dependency becomes visible.

### `/etc/hosts`

A static file, present only on the one device it's edited on (a Linux laptop here), completely independent of any DNS server. It is checked **before** any DNS lookup happens at all.

```text
192.168.1.106  splunk.lan pihole.lan dockge.lan
```

Because it bypasses DNS entirely, **this keeps working even if Pi-hole is completely down** — but only on that one device, and only until someone remembers to add the same line elsewhere.

It does not scale to new devices automatically and must be edited manually, by hand, per device.

|                             | Pi-hole local DNS                                          | `/etc/hosts`                          |
| --------------------------- | ---------------------------------------------------------- | ------------------------------------- |
| Scope                       | Every device using Pi-hole as DNS (here: whole home Wi-Fi) | One device only                       |
| Setup                       | Once, in Pi-hole's UI                                      | Manually, per device                  |
| Depends on Pi-hole being up | Yes                                                        | No                                    |
| Works on phones             | Yes (via router)                                           | Not practically — no easy file access |

## Decision summary

* Want it to "just work" on every phone/laptop on home Wi-Fi with no per-device setup → **Pi-hole local DNS** (accept the dependency on Pi-hole staying up; mitigate with `restart: unless-stopped` and a secondary DNS fallback on the router).

* Want one specific device to keep working even if Pi-hole is fully down → add an `/etc/hosts` entry on that device as a backup, in addition to Pi-hole.

* Don't want to manage DNS at all → skip naming, always use the IP directly (`http://192.168.1.106/`), accepting the inconvenience of remembering IP:port combinations.

## Verifying it's actually working

```bash
# Confirm DNS resolves (run from a device using Pi-hole as DNS)
nslookup -type=A splunk.lan 192.168.1.104

# Confirm Nginx is reachable and routing correctly, bypassing DNS entirely
curl -I http://192.168.1.106/ -H "Host: splunk.lan"

# Confirm Tailscale subnet route is approved and active
tailscale status --json | grep -A 5 PrimaryRoutes
```

If the `curl` with an explicit `Host` header works but the plain `http://splunk.lan/` does not, the problem is in DNS resolution, not Nginx — the request never reached Nginx at all.

## Tailscale DNS setup for remote `.lan` access

The following configuration allows `splunk.lan` to work while a device is connected to Tailscale over a remote network such as mobile data.

### 1. Tailscale Admin Console → DNS → Nameservers

Added:

```text
192.168.1.104
```

### 2. Enable Split DNS

Restricted the nameserver to:

```text
lan
```

This makes `.lan` DNS queries use Pi-hole.

### 3. Pop!_OS — enable Tailscale route acceptance

```bash
sudo tailscale set --accept-routes=true
```

### 4. Pop!_OS — enable Tailscale DNS

```bash
sudo tailscale set --accept-dns=true
```

### 5. Verify DNS on Pop!_OS

```bash
resolvectl query splunk.lan
```

Expected result:

```text
splunk.lan: 192.168.1.106 -- link: tailscale0
```

### 6. Verify remote access

With Tailscale enabled over mobile data:

```text
http://splunk.lan
```

Works successfully.

### Remote request flow

```text
📱 Mobile / Pop!_OS
        │
        │ Tailscale
        ▼
   Pi-hole .104
        │
        │ DNS
        │ splunk.lan → .106
        ▼
   Nginx .106
        │
        │ reverse proxy
        ▼
   Splunk .102:8000
```
