# VPN egress gateway for guests (Wi-Fi-only PVE node)

Guests (VM/CT running model experiments) need outbound internet, but the only
uplink is a phone-hotspot Wi-Fi and the VPN provider blocks plain WireGuard by
signature. Run a dedicated egress gateway as an LXC container: full-tunnel
AmneziaWG + MASQUERADE + dnsmasq, and point guests at it as their gateway and
DNS. This keeps SSH/Web to the host untouched (narrow routes, not a host-wide
VPN) and gives every guest a clean foreign egress through one place.

## Provider fact (this setup)

- **Vanilla WireGuard is blocked by signature**: handshake packets leave
the box but the peer never replies. Obfuscated **AmneziaWG (Jc/S1/H1)** passes.
- There is **no standalone release tarball** for the AmneziaWG wireguard-go
  userspace daemon. Get the static binary from the official image
  `amneziavpn/amneziawg-go` (the `awg`/`awg-quick` CLI come from
  `awg-tools`). Alternatives (building from source, a fresh VPS as a hop) were
  each rejected — they cost money or time; the Docker-image binary is the
  cheap path.
- Amnezia and vanilla configs differ and usually on the port too; don't reuse
  one config for the other.

## Architecture

```text
phone (NAT #1)
  └── PVE host 10.67.x.x      ← SSH/Web here, NAT only for the gateway addr (below)
       └── vmbr0 10.10.10.1/24
            └── LXC vpn-gw 10.10.10.2   ← awg0 (0.0.0.0/0) + MASQUERADE + dnsmasq
                 └── VPN (foreign egress)
```

Guest recipe: static `ip 10.10.10.<N>/24`, `gateway 10.10.10.2`, `dns 10.10.10.2`.
No DHCP in the gateway.

## Pitfalls (verified, each one cost real time)

- **DNS chicken-and-egg.** Endpoint resolves by domain (needs tunnel), but the
tunnel cannot reach the endpoint without resolving it. Put the endpoint IP as a
static line in the gateway's `/etc/hosts`.
- **The HOST needs NAT for the gateway itself.** The gateway's handshake to the
VPN server leaves via Wi-Fi, not through the tunnel. Removing host NAT kills
the gateway. Keep host NAT *selective* (source = only the gateway address) so
other guests can't sneak out past the VPN.
- **PVE overwrites a container's `/etc/resolv.conf` on every start** with the
host's DNS (`10.67.x.x`), which is unreachable through the tunnel. Set DNS at
the PVE level with `pct set <id> --nameserver 10.10.10.2`; editing the file
alone does not survive restart.
- **Run dnsmasq in the gateway** (`listen-address` = gw addr, `no-resolv`, app
upstream `1.1.1.1`/`8.8.8.8` via the tunnel) so guest DNS also goes through the
VPN — no DNS leak that the phone/MITM could read.
- **`pct restart <id>` does not exist.** Use `pct stop` + `pct start`.
- **TUN in an unprivileged LXC** needs `lxc.cgroup2.devices.allow: c 10:200 rwm`
plus a bind-mount of `/dev/net/tun`, or the tunnel cannot open.
- **`awg-quick` PostUp parser breaks on quotes, `>`, and complex one-liners.**
Keep PostUp to simple commands; move anything elaborate into a small script.
- **A minimal Debian LXC template ships no `iptables`**, and `awg-quick` needs
it to apply PostUp NAT — install it inside the container.
- **On a single-disk lab node, keep the gateway's rootfs on a normal `local`
storage and give it just enough RAM (512 MB) — it is a router, not a worker.**

## Verification

- From the gateway: `curl` an IP-echo service; external IP must be the foreign
VPN egress, distinct from the host's direct IP.
- From a guest behind the gateway: same IP-echo must return the VPN egress, and
resolve `api.ipify.org` through the gateway DNS.
- After `pct stop`+`pct start` of the gateway and of a guest, both must still
egress via the tunnel (proves autostart + `--nameserver` persistence).