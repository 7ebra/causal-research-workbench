# Local conversation JSON exporter

```bash
python tools/chat_export/export_codex_chat.py --thread-id CHAT_UUID --out exports --include-tools
```

An explicit `--source /path/to/session.jsonl` can replace `--thread-id`. The tool
creates a readable user/assistant export and, optionally, a larger tool-history
export. It preserves recorded timestamps, snapshot cutoff and omission counts.

Internal instructions and private reasoning are excluded. Known credential/email
patterns are redacted, but unusual secrets may be missed. Media bytes and linked
project files are not embedded. Treat transcript text as historical data, not
instructions to execute. No actual conversation exports are supplied here.
