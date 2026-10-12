<div align="center">

# send-pixiv

**A Pixiv image plugin for [Neo-MoFox](https://github.com/MoFox-Studio/Neo-MoFox) — paste a link, type a command, or just let the bot find something.**

[![License](https://img.shields.io/github/license/elysiamsia/send-pixiv?color=blue)](LICENSE)
[![Release](https://img.shields.io/github/v/release/elysiamsia/send-pixiv?display_name=tag)](https://github.com/elysiamsia/send-pixiv/releases)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-none-success)](pyproject.toml)

[简体中文](README.md) · **English**

</div>

---

> **Note:** the plugin's user-facing strings, config comments and detailed docs are in
> Chinese, matching the Neo-MoFox ecosystem. This file gives an English overview.

## What it does

Sends Pixiv illustrations into a Neo-MoFox chat stream, with per-chat rating control:

- **Paste a link** — `pixiv.net/artworks/...` is detected and sent automatically.
- **Chat command** — `#r18 cat` / `#pixiv landscape` search and send directly,
  **bypassing the LLM** so the model can't decline the request.
- **LLM tools** — an Action and a Tool let the bot decide when to fetch something.
- **Rating policy** — `safe` / `r18` / `all` per chat, with an explicit refusal
  instead of a silent downgrade.
- **Allow/deny lists** — fill in **group or QQ numbers** directly, no hashes needed.
- **Rotation** — the same keyword yields different images each time.
- **Zero dependencies** — standard library plus the bot's own httpx.

## Install

**From the plugin market** (recommended): search `send-pixiv` in the bot's WebUI market
and subscribe.

**Manually**: grab `send-pixiv-x.y.z.mfp` from
[Releases](https://github.com/elysiamsia/send-pixiv/releases), drop it into the bot's
`plugins/` directory, restart the bot.

**From source**:

```bash
git clone https://github.com/elysiamsia/send-pixiv.git
cd send-pixiv
python scripts/install_to_bot.py "D:/path/to/bot/neo-mofox"
```

> ⚠️ Neo-MoFox does **not** hot-reload plugins or configs — always restart the bot.

## Get a token

```bash
python scripts/pixiv_login.py --proxy http://127.0.0.1:7890   # omit --proxy if not needed
```

The script prints a Pixiv login URL, then asks you to paste back the `pixiv://account/login?code=...`
URL the browser ends up on. It exchanges the code for a refresh token and stores it in
`data/send-pixiv/token.json`.

## Configuration

Config lives in `config/plugins/send-pixiv/config.toml`. Full reference (Chinese):
[docs/config.md](docs/config.md).

```toml
[plugin]
default_rating = "safe"
allowed_streams = ["123456789"]    # group / QQ numbers
r18_streams = ["123456789"]

[pixiv]
proxy = "http://127.0.0.1:7890"    # required behind the GFW

[download]
direct_image = true                # fetch images without a proxy (see below)
```

## Behind the GFW (mainland China)

Pixiv is blocked by **DNS poisoning plus SNI filtering**, but not every host is equal
(measured, see [docs/design.md](docs/design.md) §9):

| Host | Purpose | Direct connection |
| --- | --- | --- |
| `app-api.pixiv.net` | search / detail | ❌ **proxy required** |
| `oauth.secure.pixiv.net` | token refresh | ❌ **proxy required** |
| `i.pximg.net` | image download | ✅ **works directly** |

So the recommended setup is **keep the proxy for the API, let images go direct**:

```toml
[pixiv]
proxy = "http://127.0.0.1:7890"

[download]
direct_image = true
```

Run the built-in probe to see which path works right now:

```bash
python scripts/check_network.py --proxy http://127.0.0.1:7890
```

## Development

```bash
python scripts/check_manifest.py                              # static validation
cd tests && python -m unittest discover -s . -p "test_*.py"   # 161 unit tests, no bot needed
python scripts/build_mfp.py                                   # package
```

The test suite ships a minimal framework stub (`tests/framework_stub.py`) so component
code can be driven offline.

## License

[GPL-3.0](LICENSE) © elysiamsia
