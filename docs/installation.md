# Installation

## Requirements

- Python **3.10 or newer**.
- A working `pip`. No system packages needed.
- Internet access at install time (to fetch the package + dependencies from PyPI/GitHub).

The runtime dependency closure is small and pure-Python:

- [`openai`](https://pypi.org/project/openai/) — used as a generic OpenAI-compatible HTTP client (we don't tie to OpenAI as a vendor).
- [`pyyaml`](https://pypi.org/project/PyYAML/) — config parsing (`yaml.safe_load` only).
- Transitive: `httpx`, `httpcore`, `anyio`, `certifi`, `idna`, `h11`, `pydantic`, `distro`, `jiter`, `sniffio`, `tqdm`.

## From GitHub (recommended)

```bash
pip install git+https://github.com/Arcanada-one/coworker
```

This installs the `coworker` console-script entry point on `$PATH`.

## From source (development)

```bash
git clone https://github.com/Arcanada-one/coworker
cd coworker
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

`[dev]` adds `ruff`, `pytest`, and `pip-audit`. Run the test suite:

```bash
pytest -q
ruff check .
```

## Configuration files

`coworker` reads two YAML files at startup. They live under XDG-standard paths:

| File                                       | Default path (Linux/macOS)               |
| ------------------------------------------ | ---------------------------------------- |
| `providers.yaml`                           | `~/.config/coworker/providers.yaml`      |
| `profiles.yaml`                            | `~/.config/coworker/profiles.yaml`       |

If `$XDG_CONFIG_HOME` is set, the directory becomes `$XDG_CONFIG_HOME/coworker/` instead.

Bootstrap from the shipped examples:

```bash
mkdir -p ~/.config/coworker
curl -fsSL https://raw.githubusercontent.com/Arcanada-one/coworker/main/examples/providers.yaml.example \
  > ~/.config/coworker/providers.yaml
curl -fsSL https://raw.githubusercontent.com/Arcanada-one/coworker/main/examples/profiles.yaml.example \
  > ~/.config/coworker/profiles.yaml
```

Both files **must exist** before the first call — `coworker ask` raises `FileNotFoundError` otherwise. Stats / debug / `--help` work without them.

## State files

| File or directory                                | Purpose                                  |
| ------------------------------------------------ | ---------------------------------------- |
| `~/.local/state/coworker/log/YYYY-MM-DD.jsonl`   | Per-call metadata (always written, unless `--no-log` / `COWORKER_NO_LOG=1`). |
| `~/.local/state/coworker/blobs/sha256/<ab>/...`  | Optional sha256-deduplicated corpus blobs (only if `COWORKER_LOG_CORPUS=1`). |

`$XDG_STATE_HOME` is honoured: if set, the root becomes `$XDG_STATE_HOME/coworker/`.

State directories are created on first write. You can safely `rm -rf ~/.local/state/coworker/log/` to wipe history; nothing else depends on it.

## Verifying the install

```bash
coworker --help
coworker ask --help
```

Both should exit 0 and print help text. If `coworker: command not found`, your `pip install` location is not on `$PATH` — `python -m coworker.cli --help` works as a fallback.

## Uninstalling

```bash
pip uninstall coworker
rm -rf ~/.config/coworker ~/.local/state/coworker
```
